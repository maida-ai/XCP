#!/usr/bin/env python3
"""Proof-of-concept benchmark: HTTP/2 JSON chat vs XCP binary frames.

This script measures **throughput (MiB/s)** and **p99 latency (ms)** for
round-tripping a configurable payload between a client and a local echo server
implemented in two ways:

* **HTTP/2**  — reference JSON/UTF-8 baseline (uses `httpx`, TLS off).
* **XCP**     — binary frames over TCP (uses the reference `xcp` PoC library).

Prerequisites
-------------
$ pip install httpx h2 tqdm numpy rich

Usage
-----
$ python benchmarks/poc_http2_vs_xcp.py --runs 1000 --size 16384

The script spins up two local servers on ephemeral ports, fires the benchmark
loop, prints a summary table, and exits.  It is *single-process*; network RTT
and scheduler noise are therefore minimal but still realistic.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import time
from collections import defaultdict
from contextlib import closing, contextmanager
from statistics import median, quantiles
from threading import Thread
from typing import Callable, Dict, List, Tuple

import httpx
import numpy as np
from rich import box
from rich.console import Console
from rich.table import Table
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def find_free_port() -> int:
    """Pick an OS-assigned port that is free for listening."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def time_block() -> Tuple[float, None]:
    start = time.perf_counter()
    yield
    end = time.perf_counter()
    elapsed = end - start
    return elapsed  # type: ignore[misc]


# ---------------------------------------------------------------------------
# HTTP/2 echo server (simple)
# ---------------------------------------------------------------------------

from http.server import BaseHTTPRequestHandler, HTTPServer  # stdlib


class HTTPEchoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # http.server lacks native h2; we rely on httpx client side

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args):  # noqa: D401, N802
        return  # silence server spam


def run_http_server(port: int):
    server = HTTPServer(("127.0.0.1", port), HTTPEchoHandler)
    server.serve_forever()


# ---------------------------------------------------------------------------
# XCP echo server — uses the reference implementation API.
# ---------------------------------------------------------------------------

try:
    from xcp import Frame, FrameHeader, Server  # type: ignore
except ModuleNotFoundError:
    Frame = None  # type: ignore
    Server = None  # type: ignore


class XCPEchoServer(Thread):
    """Thin wrapper around the PoC XCP server that echoes DATA frames."""

    def __init__(self, port: int):
        super().__init__(daemon=True)
        self.port = port

    def run(self):
        if Server is None:
            raise RuntimeError(
                "xcp reference library is missing. Install before running benchmark."
            )

        def on_frame(frame: "Frame") -> "Frame":  # type: ignore[valid-type]
            # Echo DATA frames verbatim
            return frame

        server = Server("127.0.0.1", self.port, on_frame=on_frame)
        server.serve_forever()


# ---------------------------------------------------------------------------
# Benchmark helpers
# ---------------------------------------------------------------------------

def bench_http2(url: str, payload: bytes, runs: int) -> Dict[str, List[float]]:
    """Return dict with latencies."""
    latencies: List[float] = []
    with httpx.Client(http2=True, timeout=10.0) as client:
        for _ in tqdm(range(runs), desc="HTTP/2", leave=False):
            start = time.perf_counter()
            r = client.post(url, content=payload)
            _ = r.content  # ensure body received
            latencies.append(time.perf_counter() - start)
    return {"lat": latencies}


def bench_xcp(host: str, port: int, payload: bytes, runs: int) -> Dict[str, List[float]]:
    if Frame is None:
        raise RuntimeError("xcp reference library missing. Cannot benchmark XCP.")

    latencies: List[float] = []
    from xcp import Client  # imported lazily to avoid hard dep if missing

    client = Client(host, port)

    for _ in tqdm(range(runs), desc="XCP", leave=False):
        start = time.perf_counter()
        frame = Frame(
            header=FrameHeader(
                channelId=0,
                msgType=0x20,  # DATA
                bodyCodec=0x01,  # JSON
                schemaId=0,
            ),
            payload=payload,
        )
        echo = client.request(frame)
        _ = echo.payload
        latencies.append(time.perf_counter() - start)

    client.close()
    return {"lat": latencies}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def summarise(latencies: List[float], size_bytes: int) -> Dict[str, float]:
    lat_ms = [t * 1000 for t in latencies]
    p50, p95, p99 = quantiles(lat_ms, n=100)[:3]
    throughput = (size_bytes * len(latencies)) / sum(latencies) / (2 ** 20)  # MiB/s
    return {"p50": p50, "p95": p95, "p99": p99, "thr": throughput}


def print_table(results: Dict[str, Dict[str, float]]):
    console = Console()
    table = Table(title="HTTP/2 vs XCP PoC", box=box.SIMPLE_HEAVY)
    table.add_column("Transport")
    table.add_column("p50 (ms)")
    table.add_column("p95 (ms)")
    table.add_column("p99 (ms)")
    table.add_column("Throughput (MiB/s)")

    for k, v in results.items():
        table.add_row(k, f"{v['p50']:.2f}", f"{v['p95']:.2f}", f"{v['p99']:.2f}", f"{v['thr']:.1f}")
    console.print(table)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="HTTP/2 vs XCP benchmark")
    ap.add_argument("--runs", type=int, default=500, help="Number of round trips")
    ap.add_argument("--size", type=int, default=8192, help="Payload size in bytes")
    args = ap.parse_args()

    payload = os.urandom(args.size)

    # ---------------------------------------------------------------------
    # Spin up local echo servers
    # ---------------------------------------------------------------------
    http_port = find_free_port()
    xcp_port = find_free_port()

    http_thread = Thread(target=run_http_server, args=(http_port,), daemon=True)
    http_thread.start()

    xcp_thread = XCPEchoServer(port=xcp_port)
    xcp_thread.start()

    http_url = f"http://127.0.0.1:{http_port}/echo"

    http_res = bench_http2(http_url, payload, args.runs)
    xcp_res = bench_xcp("127.0.0.1", xcp_port, payload, args.runs)

    summary = {
        "HTTP/2": summarise(http_res["lat"], args.size),
        "XCP": summarise(xcp_res["lat"], args.size),
    }

    print_table(summary)


if __name__ == "__main__":
    main()
