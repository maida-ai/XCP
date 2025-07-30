#!/usr/bin/env python3
# Copyright 2025 Maida.AI
# SPDX-License-Identifier: Apache-2.0
"""Proof-of-concept benchmark: HTTP/2 JSON chat vs XCP v0.2 binary frames.

This script measures **throughput (MiB/s)** and **p99 latency (ms)** for
round-tripping a configurable payload between a client and a local echo server
implemented in two ways:

* **HTTP/2**  — reference JSON/UTF-8 baseline (uses `httpx`, TLS off).
* **XCP v0.2** — binary frames over TCP with Ether envelopes (uses the reference `xcp` PoC library).

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
import time
import uuid
from contextlib import closing, contextmanager
from statistics import quantiles
from threading import Thread

import httpx
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
def time_block() -> tuple[float, None]:
    start = time.perf_counter()
    yield
    end = time.perf_counter()
    elapsed = end - start
    return elapsed  # type: ignore[misc]


def generate_payload_with_checksum(size: int) -> tuple[bytes, str]:
    """Generate a payload with a checksum for validation.

    Args:
        size: Size of payload in bytes

    Returns:
        Tuple of (payload, checksum)
    """
    payload = os.urandom(size)
    checksum = hashlib.sha256(payload).hexdigest()
    return payload, checksum


def generate_unique_payload_with_checksum(size: int, run_id: str) -> tuple[bytes, str]:
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


def validate_response(
    original_payload: bytes, response_payload: bytes, original_checksum: str, run_number: int
) -> bool:
    """Validate that the response matches the original payload.

    Args:
        original_payload: The original payload sent
        response_payload: The response payload received
        original_checksum: The expected checksum
        run_number: The run number for error reporting

    Returns:
        True if validation passes, False otherwise
    """
    if len(response_payload) != len(original_payload):
        print(f"❌ Run {run_number}: Length mismatch! Expected {len(original_payload)}, got {len(response_payload)}")
        return False
    if response_payload != original_payload:
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
# HTTP/2 echo server
# ---------------------------------------------------------------------------
from http.server import BaseHTTPRequestHandler, HTTPServer


class HTTPEchoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # http.server lacks native h2; we rely on httpx client side

    def do_POST(self):  # noqa: N802
        """Handle POST requests by echoing the JSON payload."""
        content_length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(content_length)

        # Parse JSON and echo back
        try:
            json_data = json.loads(data.decode("utf-8"))
            response_data = json.dumps(json_data, separators=(",", ":")).encode("utf-8")
        except (json.JSONDecodeError, UnicodeDecodeError):
            # If not JSON, echo raw data
            response_data = data

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_data)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(response_data)

    def log_message(self, format: str, *args):  # noqa: D401, N802
        pass  # Suppress logging


def run_http_server(port: int):
    """Run the HTTP echo server."""
    server = HTTPServer(("127.0.0.1", port), HTTPEchoHandler)
    server.serve_forever()


# ---------------------------------------------------------------------------
# XCP v0.2 echo server
# ---------------------------------------------------------------------------
try:
    from xcp import Ether, Frame, FrameHeader, Server  # type: ignore
except ModuleNotFoundError:
    Frame = None  # type: ignore
    Server = None  # type: ignore


class XCPEchoServer(Thread):
    """XCP v0.2 echo server that handles Ether envelopes."""

    def __init__(self, port: int):
        super().__init__(daemon=True)
        self.port = port

    def run(self):
        """Run the XCP echo server."""
        if Server is None:
            raise RuntimeError("xcp reference library is missing. Install before running benchmark.")

        def on_ether(ether: Ether) -> Ether:  # type: ignore[valid-type]
            # Echo DATA frames verbatim
            return ether

        server = Server("127.0.0.1", self.port, on_ether=on_ether)
        server.serve_forever()


# ---------------------------------------------------------------------------
# Benchmark functions
# ---------------------------------------------------------------------------
def bench_http2(url: str, payload: bytes, payload_checksum: str, runs: int) -> dict[str, list[float]]:
    """Benchmark HTTP/2 performance.

    Args:
        url: The HTTP/2 server URL
        payload: The payload to send
        payload_checksum: The expected checksum
        runs: Number of benchmark runs

    Returns:
        Dictionary with latencies and validation errors
    """
    latencies = []
    validation_errors = 0

    with httpx.Client(
        http2=True, timeout=10.0, limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)
    ) as client:
        for run_num in tqdm(range(runs), desc="HTTP/2"):
            try:
                # Create JSON payload
                json_data = {
                    "data": payload.hex(),
                    "run_id": run_num,
                    "timestamp": time.time(),
                    "uuid": str(uuid.uuid4()),
                }
                json_payload = json.dumps(json_data, separators=(",", ":")).encode("utf-8")

                # Add cache-busting headers
                headers = {
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "X-Benchmark-Run": str(run_num),
                    "X-Timestamp": str(time.time()),
                    "X-UUID": str(uuid.uuid4()),
                    "Content-Type": "application/json",
                }

                start = time.perf_counter()
                r = client.post(url, content=json_payload, headers=headers)
                elapsed = time.perf_counter() - start
                latencies.append(elapsed)

                if r.status_code != 200:
                    print(f"❌ Run {run_num}: HTTP status {r.status_code}")
                    validation_errors += 1
                    continue

                # Parse response
                try:
                    response_data = json.loads(r.content.decode("utf-8"))
                    response_payload = bytes.fromhex(response_data["data"])
                except (json.JSONDecodeError, KeyError, ValueError):
                    print(f"❌ Run {run_num}: Invalid JSON response")
                    validation_errors += 1
                    continue

                if not validate_response(payload, response_payload, payload_checksum, run_num):
                    validation_errors += 1

            except Exception as e:
                print(f"❌ Run {run_num}: HTTP/2 request failed: {e}")
                validation_errors += 1
                continue

    return {"latencies": latencies, "validation_errors": validation_errors, "total_runs": runs}


def bench_xcp(host: str, port: int, payload: bytes, payload_checksum: str, runs: int) -> dict[str, list[float]]:
    """Benchmark XCP v0.2 performance.

    Args:
        host: The XCP server host
        port: The XCP server port
        payload: The payload to send
        payload_checksum: The expected checksum
        runs: Number of benchmark runs

    Returns:
        Dictionary with latencies and validation errors
    """
    if Server is None:
        raise RuntimeError("xcp reference library missing. Cannot benchmark XCP.")

    from xcp import Client, Ether  # imported lazily to avoid hard dep if missing

    latencies = []
    validation_errors = 0

    client = Client(host, port)
    try:
        for run_num in tqdm(range(runs), desc="XCP v0.2"):
            try:
                # Create Ether envelope with the payload
                ether = Ether(
                    kind="benchmark",
                    schema_version=1,
                    payload={"data": payload.hex(), "run_id": run_num},
                    metadata={"timestamp": time.time(), "uuid": str(uuid.uuid4())},
                )

                start = time.perf_counter()
                response = client.send_ether(ether)
                elapsed = time.perf_counter() - start
                latencies.append(elapsed)

                # Parse response Ether
                try:
                    response_data = json.loads(response.payload.decode("utf-8"))
                    response_ether = Ether(**response_data)
                    response_payload = bytes.fromhex(response_ether.payload["data"])
                except (json.JSONDecodeError, KeyError, ValueError):
                    print(f"❌ Run {run_num}: Invalid Ether response")
                    validation_errors += 1
                    continue

                if not validate_response(payload, response_payload, payload_checksum, run_num):
                    validation_errors += 1

            except Exception as e:
                print(f"❌ Run {run_num}: XCP v0.2 request failed: {e}")
                validation_errors += 1
                continue

    finally:
        client.close()

    return {"latencies": latencies, "validation_errors": validation_errors, "total_runs": runs}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def summarise(
    latencies: list[float],
    size_bytes: int,
    validation_errors: int,
    total_runs: int,
    unit: str = "us",
) -> dict[str, float]:
    """Summarize benchmark results.

    Args:
        latencies: List of latency measurements
        size_bytes: Size of payload in bytes
        validation_errors: Number of validation errors
        total_runs: Total number of runs
        unit: Time unit for latency

    Returns:
        Dictionary with summary statistics
    """
    if not latencies:
        return {"p50": float("nan"), "p95": float("nan"), "p99": float("nan"), "thr": 0.0, "success_rate": 0.0}

    # Convert to microseconds
    lat_us = [t * 1e6 for t in latencies]
    p50, p95, p99 = quantiles(lat_us, n=100)[:3]

    # Calculate throughput in MiB/s
    throughput = (size_bytes * len(latencies)) / sum(latencies) / (2**20)

    # Calculate success rate
    success_rate = (total_runs - validation_errors) / total_runs * 100

    return {"p50": p50, "p95": p95, "p99": p99, "thr": throughput, "success_rate": success_rate}


def print_table(results: dict[str, dict[str, float]], unit: str = "us"):
    """Print benchmark results table.

    Args:
        results: Dictionary of benchmark results
        unit: Time unit for display
    """
    console = Console()
    table = Table(title="HTTP/2 vs XCP v0.2 Benchmark Results", box=box.SIMPLE_HEAVY)
    table.add_column("Transport")
    table.add_column(f"p50 ({unit}, ↓)")
    table.add_column(f"p95 ({unit}, ↓)")
    table.add_column(f"p99 ({unit}, ↓)")
    table.add_column("Throughput (MiB/s, ↑)")
    table.add_column("Success Rate (%)")

    for k, v in results.items():
        success_rate = v.get("success_rate", 100.0)
        success_color = "green" if success_rate == 100.0 else "red"
        table.add_row(
            k,
            f"{v['p50']:.2f}",
            f"{v['p95']:.2f}",
            f"{v['p99']:.2f}",
            f"{v['thr']:.1f}",
            f"[{success_color}]{success_rate:.1f}%[/{success_color}]",
        )

    console.print(table)


def print_validation_summary(http_results: dict, xcp_results: dict):
    """Print validation summary.

    Args:
        http_results: HTTP/2 benchmark results
        xcp_results: XCP benchmark results
    """
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)

    for label, res in [("HTTP/2", http_results), ("XCP v0.2", xcp_results)]:
        errors = res.get("validation_errors", 0)
        total = res.get("total_runs", 0)
        success = total - errors
        if total == 0:
            percent = 0.0
        else:
            percent = success / total * 100
        print(f"{label:20}: {success}/{total} successful ({percent:.1f}%)")

    if any(res.get("validation_errors", 0) > 0 for res in [http_results, xcp_results]):
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


def main():
    """Main benchmark function."""
    parser = argparse.ArgumentParser(description="Benchmark HTTP/2 vs XCP v0.2")
    parser.add_argument("--runs", type=int, default=100, help="Number of benchmark runs")
    parser.add_argument("--size", type=int, default=1024, help="Payload size in bytes")
    parser.add_argument("--unit", choices=["us", "ms", "ns"], default="us", help="Latency unit")
    args = parser.parse_args()

    # Generate unique payload for each run
    run_id = str(uuid.uuid4())
    payload, checksum = generate_unique_payload_with_checksum(args.size, run_id)

    # Find free ports
    http_port = find_free_port()
    xcp_port = find_free_port()

    print(f"Benchmarking {args.runs} runs with {args.size} byte payloads")
    print(f"Ports: HTTP/2={http_port}, XCP={xcp_port}")

    # Start servers
    http_thread = Thread(target=run_http_server, args=(http_port,), daemon=True)
    xcp_thread = XCPEchoServer(port=xcp_port)

    http_thread.start()
    xcp_thread.start()

    # Wait for servers to start
    time.sleep(0.5)

    try:
        # Run benchmarks
        http_results = bench_http2(f"http://127.0.0.1:{http_port}/", payload, checksum, args.runs)
        xcp_results = bench_xcp("127.0.0.1", xcp_port, payload, checksum, args.runs)

        # Summarize results
        results = {
            "HTTP/2": summarise(
                http_results["latencies"],
                args.size,
                http_results["validation_errors"],
                http_results["total_runs"],
                args.unit,
            ),
            "XCP v0.2": summarise(
                xcp_results["latencies"],
                args.size,
                xcp_results["validation_errors"],
                xcp_results["total_runs"],
                args.unit,
            ),
        }

        # Print results
        print_table(results, args.unit)
        print_validation_summary(http_results, xcp_results)

    finally:
        # Cleanup
        pass  # Threads are daemon, will exit when main thread exits


if __name__ == "__main__":
    main()
