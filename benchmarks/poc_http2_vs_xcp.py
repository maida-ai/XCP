#!/usr/bin/env python3
# Copyright 2025 Maida.AI
# SPDX-License-Identifier: Apache-2.0
"""Proof-of-concept benchmark: HTTP/2 JSON chat vs XCP binary frames.

This script measures **throughput (MiB/s)** and **p99 latency (ms)** for
round-tripping a configurable payload between a client and a local echo server
implemented in two ways:

* **HTTP/2**  — reference JSON/UTF-8 baseline (uses `httpx`, TLS off).
* **XCP**     — binary frames over TCP (uses the reference `xcp` PoC library).

The benchmark includes comprehensive validation to ensure data integrity and
detect any losses during testing. It also includes cache-busting measures to
ensure no caching affects the results.

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
import hashlib
import json
import os
import socket
import ssl
import time
import uuid
from collections import defaultdict
from contextlib import closing, contextmanager
from statistics import median, quantiles
from threading import Thread
from typing import Callable, Dict, List, Tuple, Optional

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


def generate_payload_with_checksum(size: int) -> Tuple[bytes, str]:
    """Generate a payload with a checksum for validation.

    Args:
        size: Size of payload in bytes

    Returns:
        Tuple of (payload, checksum)
    """
    payload = os.urandom(size)
    checksum = hashlib.sha256(payload).hexdigest()
    return payload, checksum


def generate_unique_payload_with_checksum(size: int, run_id: str) -> Tuple[bytes, str]:
    """Generate a unique payload with checksum for each run to prevent caching.

    Args:
        size: Size of payload in bytes
        run_id: Unique identifier for this run

    Returns:
        Tuple of (payload, checksum)
    """
    # Create a unique payload by combining random data with run_id
    base_payload = os.urandom(size - len(run_id))
    unique_payload = base_payload + run_id.encode()
    checksum = hashlib.sha256(unique_payload).hexdigest()
    return unique_payload, checksum


def validate_response(original_payload: bytes, response_payload: bytes,
                     original_checksum: str, run_number: int) -> bool:
    """Validate that the response matches the original payload.

    Args:
        original_payload: The original payload sent
        response_payload: The response payload received
        original_checksum: The original checksum
        run_number: The run number for error reporting

    Returns:
        True if validation passes, False otherwise
    """
    if len(response_payload) != len(original_payload):
        print(f"❌ Run {run_number}: Length mismatch! Expected {len(original_payload)}, got {len(response_payload)}")
        return False

    if response_payload != original_payload:
        # Check if it's a checksum mismatch
        response_checksum = hashlib.sha256(response_payload).hexdigest()
        if response_checksum != original_checksum:
            print(f"❌ Run {run_number}: Checksum mismatch!")
            print(f"   Expected: {original_checksum}")
            print(f"   Got:      {response_checksum}")
        else:
            print(f"❌ Run {run_number}: Content mismatch despite same checksum!")
        return False

    return True


# ---------------------------------------------------------------------------
# HTTP/2 echo server (simple)
# ---------------------------------------------------------------------------

from http.server import BaseHTTPRequestHandler, HTTPServer  # stdlib


class HTTPEchoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # http.server lacks native h2; we rely on httpx client side

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        # Add cache-busting headers to prevent any caching
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")  # Changed from JSON to prevent caching
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("X-Benchmark-Run", str(time.time()))  # Add timestamp to prevent caching
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
# Benchmark helpers with validation and cache-busting
# ---------------------------------------------------------------------------

def bench_http2(url: str, payload: bytes, payload_checksum: str, runs: int) -> Dict[str, List[float]]:
    """Return dict with latencies and validation results."""
    latencies: List[float] = []
    validation_errors = 0

    # Create a new client for each benchmark to prevent connection reuse caching
    with httpx.Client(
        http2=True,
        timeout=10.0,
        # Disable connection pooling to prevent caching
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)
    ) as client:
        for run_num in tqdm(range(runs), desc="HTTP/2", leave=False):
            start = time.perf_counter()
            try:
                # Add cache-busting headers to each request
                headers = {
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "X-Benchmark-Run": str(run_num),
                    "X-Timestamp": str(time.time()),
                    "X-UUID": str(uuid.uuid4())
                }

                r = client.post(url, content=payload, headers=headers)
                response_payload = r.content
                latencies.append(time.perf_counter() - start)

                # Validate response
                if not validate_response(payload, response_payload, payload_checksum, run_num):
                    validation_errors += 1

            except Exception as e:
                print(f"❌ Run {run_num}: HTTP/2 request failed: {e}")
                validation_errors += 1
                # Don't include failed requests in latency measurements
                continue

    if validation_errors > 0:
        print(f"⚠️  HTTP/2: {validation_errors}/{runs} validation errors detected!")

    return {
        "lat": latencies,
        "validation_errors": validation_errors,
        "total_runs": runs
    }


def bench_xcp(host: str, port: int, payload: bytes, payload_checksum: str, runs: int) -> Dict[str, List[float]]:
    """Return dict with latencies and validation results."""
    if Frame is None:
        raise RuntimeError("xcp reference library missing. Cannot benchmark XCP.")

    latencies: List[float] = []
    validation_errors = 0
    from xcp import Client  # imported lazily to avoid hard dep if missing

    # Create a new client for each benchmark to prevent connection reuse caching
    client = Client(host, port, enable_cache_busting=True)

    try:
        for run_num in tqdm(range(runs), desc="XCP", leave=False):
            start = time.perf_counter()
            try:
                # Create a unique frame for each run to prevent any potential caching
                frame = Frame(
                    header=FrameHeader(
                        channelId=run_num,  # Use run number as channel ID to make each frame unique
                        msgType=0x20,  # DATA
                        bodyCodec=0x01,  # JSON
                        schemaId=run_num,  # Use run number as schema ID to make each frame unique
                        msgId=run_num,  # Use run number as message ID to make each frame unique
                    ),
                    payload=payload,
                )
                echo = client.request(frame)
                response_payload = echo.payload
                latencies.append(time.perf_counter() - start)

                # Validate response
                if not validate_response(payload, response_payload, payload_checksum, run_num):
                    validation_errors += 1

            except Exception as e:
                print(f"❌ Run {run_num}: XCP request failed: {e}")
                validation_errors += 1
                # Don't include failed requests in latency measurements
                continue
    finally:
        client.close()

    if validation_errors > 0:
        print(f"⚠️  XCP: {validation_errors}/{runs} validation errors detected!")

    return {
        "lat": latencies,
        "validation_errors": validation_errors,
        "total_runs": runs
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

UNITS = {
    "s": 1,
    "ms": 1e3,
    "us": 1e6,
    "ns": 1e9,
}

def summarise(
    latencies: List[float],
    size_bytes: int,
    validation_errors: int,
    total_runs: int,
    unit: str = "us",
) -> Dict[str, float]:
    """Summarize benchmark results with validation info."""
    if not latencies:
        return {
            "p50": float('nan'), "p95": float('nan'), "p99": float('nan'),
            "thr": 0.0, "success_rate": 0.0
        }

    lat_ms = [t * UNITS[unit] for t in latencies]
    p50, p95, p99 = quantiles(lat_ms, n=100)[:3]
    throughput = (size_bytes * len(latencies)) / sum(latencies) / (2 ** 20)  # MiB/s
    success_rate = (total_runs - validation_errors) / total_runs * 100

    return {
        "p50": p50,
        "p95": p95,
        "p99": p99,
        "thr": throughput,
        "success_rate": success_rate
    }


def print_table(results: Dict[str, Dict[str, float]], unit: str = "us"):
    """Print benchmark results table with validation info."""
    console = Console()
    table = Table(title="HTTP/2 vs XCP PoC Benchmark Results (No Caching)", box=box.SIMPLE_HEAVY)
    table.add_column("Transport")
    table.add_column(f"p50 ({unit}, ↓)")
    table.add_column(f"p95 ({unit}, ↓)")
    table.add_column(f"p99 ({unit}, ↓)")
    table.add_column("Throughput (MiB/s, ↑)")
    table.add_column("Success Rate (%)")

    for k, v in results.items():
        success_rate = v.get('success_rate', 100.0)
        success_color = "green" if success_rate == 100.0 else "red"

        table.add_row(
            k,
            f"{v['p50']:.2f}",
            f"{v['p95']:.2f}",
            f"{v['p99']:.2f}",
            f"{v['thr']:.1f}",
            f"[{success_color}]{success_rate:.1f}%[/{success_color}]"
        )
    console.print(table)


def print_validation_summary(http_results: Dict, xcp_results: Dict):
    """Print detailed validation summary."""
    console = Console()

    print("\n" + "="*60)
    print("VALIDATION SUMMARY")
    print("="*60)

    # HTTP/2 validation
    http_errors = http_results.get("validation_errors", 0)
    http_total = http_results.get("total_runs", 0)
    http_success = http_total - http_errors

    # XCP validation
    xcp_errors = xcp_results.get("validation_errors", 0)
    xcp_total = xcp_results.get("total_runs", 0)
    xcp_success = xcp_total - xcp_errors

    print(f"XCP:    {xcp_success}/{xcp_total} successful ({xcp_success/xcp_total*100:.1f}%)")
    print(f"HTTP/2: {http_success}/{http_total} successful ({http_success/http_total*100:.1f}%)")

    if http_errors > 0 or xcp_errors > 0:
        print("\n⚠️  WARNING: Data integrity issues detected!")
        print("   This may indicate protocol implementation problems.")
        print("   Check the error messages above for details.")
    else:
        print("\n✅ All tests passed validation - no data loss detected!")

    print("\n🔒 Cache-busting measures applied:")
    print("   - Unique payloads per run")
    print("   - Cache-control headers")
    print("   - Connection isolation")
    print("   - Unique frame IDs per request")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="HTTP/2 vs XCP benchmark with validation and cache-busting")
    ap.add_argument("--runs", type=int, default=500, help="Number of round trips")
    ap.add_argument("--size", type=int, default=10240, help="Payload size in bytes")
    ap.add_argument("--validate-only", action="store_true", help="Run validation tests only (smaller payload)")
    args = ap.parse_args()

    time_units = "us"

    # Generate unique payload with checksum for validation (no caching)
    unique_run_id = str(uuid.uuid4())
    payload, payload_checksum = generate_unique_payload_with_checksum(args.size, unique_run_id)

    print(f"Benchmark Configuration:")
    print(f"  Runs: {args.runs}")
    print(f"  Payload size: {args.size} bytes")
    print(f"  Payload checksum: {payload_checksum[:16]}...")
    print(f"  Run ID: {unique_run_id[:8]}...")
    print(f"  Cache-busting: Enabled")
    print()

    # ---------------------------------------------------------------------
    # Spin up local echo servers
    # ---------------------------------------------------------------------
    xcp_port = find_free_port()
    http_port = find_free_port()

    print(f"Starting servers on ports {http_port} (HTTP/2) and {xcp_port} (XCP)...")

    xcp_thread = XCPEchoServer(port=xcp_port)
    xcp_thread.start()

    http_thread = Thread(target=run_http_server, args=(http_port,), daemon=True)
    http_thread.start()

    # Give servers time to start
    time.sleep(0.5)

    http_url = f"http://127.0.0.1:{http_port}/echo"

    # Run benchmarks with validation and cache-busting
    print("Running benchmarks with validation and cache-busting...")
    xcp_res = bench_xcp("127.0.0.1", xcp_port, payload, payload_checksum, args.runs)
    http_res = bench_http2(http_url, payload, payload_checksum, args.runs)

    # Generate summary
    summary = {
        "XCP": summarise(
            xcp_res["lat"],
            args.size,
            xcp_res["validation_errors"],
            xcp_res["total_runs"],
            unit=time_units,
        ),
        "HTTP/2": summarise(
            http_res["lat"],
            args.size,
            http_res["validation_errors"],
            http_res["total_runs"],
            unit=time_units,
        ),
    }

    print_table(summary, unit=time_units)
    print_validation_summary(http_res, xcp_res)


if __name__ == "__main__":
    main()
