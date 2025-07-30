#!/usr/bin/env python3
# Copyright 2025 Maida.AI
# SPDX-License-Identifier: Apache-2.0
"""Multi-codec benchmark: HTTP/2 vs XCP v0.2 with different payload types.

This script measures performance across different codecs and payload types:
- JSON payloads (text-based)
- Binary payloads (F16 tensors)

Usage:
  $ python benchmarks/poc_http2_vs_xcp_multi.py --runs 100 --size 1024
"""
from __future__ import annotations

import argparse
import hashlib
import json
import socket
import time
import uuid
from contextlib import closing
from statistics import quantiles
from threading import Thread

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
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def generate_json_payload(size: int) -> tuple[bytes, str]:
    # Generate a random float array and encode as JSON
    num_floats = size // 8  # Approximate size for JSON encoding
    data = np.random.rand(num_floats).tolist()
    payload = json.dumps({"data": data, "timestamp": time.time()}, separators=(",", ":")).encode()
    checksum = hashlib.sha256(payload).hexdigest()
    return payload, checksum


def generate_f16_payload(size: int) -> tuple[bytes, str]:
    arr = np.random.rand(size // 2).astype(np.float16)  # float16 = 2 bytes
    payload = arr.tobytes()
    checksum = hashlib.sha256(payload).hexdigest()
    return payload, checksum


def validate_response(
    original_payload: bytes, response_payload: bytes, original_checksum: str, run_number: int
) -> bool:
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
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(content_length)

        # Echo back the data
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args):
        pass  # Suppress logging


def run_http_server(port: int):
    server = HTTPServer(("127.0.0.1", port), HTTPEchoHandler)
    server.serve_forever()


# ---------------------------------------------------------------------------
# XCP v0.2 echo server
# ---------------------------------------------------------------------------
try:
    from xcp import Ether, Server
except ModuleNotFoundError:
    Server = None
    Ether = None


class XCPEchoServer(Thread):
    def __init__(self, port: int):
        super().__init__(daemon=True)
        self.port = port

    def run(self):
        if Server is None:
            raise RuntimeError("xcp reference library is missing. Install before running benchmark.")

        def on_ether(ether):
            # Echo the Ether envelope
            return ether

        server = Server("127.0.0.1", self.port, on_ether=on_ether)
        server.serve_forever()


# ---------------------------------------------------------------------------
# Benchmark helpers with validation and cache-busting
# ---------------------------------------------------------------------------
def bench_http2(url: str, payload: bytes, payload_checksum: str, runs: int, codec: str) -> dict:
    latencies = []
    validation_errors = 0

    with httpx.Client(
        http2=True, timeout=10.0, limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)
    ) as client:
        for run_num in tqdm(range(runs), desc=f"HTTP/2 + {codec.upper()}"):
            try:
                headers = {
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "X-Benchmark-Run": str(run_num),
                    "X-Timestamp": str(time.time()),
                    "X-UUID": str(uuid.uuid4()),
                    "Content-Type": "application/json" if codec == "json" else "application/octet-stream",
                }

                start = time.perf_counter()
                resp = client.post(url, content=payload, headers=headers)
                elapsed = time.perf_counter() - start
                latencies.append(elapsed)

                if resp.status_code != 200:
                    print(f"❌ Run {run_num}: HTTP status {resp.status_code}")
                    validation_errors += 1
                    continue
                response_payload = resp.content
                if not validate_response(payload, response_payload, payload_checksum, run_num):
                    validation_errors += 1
            except Exception as e:
                print(f"❌ Run {run_num}: HTTP/2 request failed: {e}")
                validation_errors += 1
                continue
    return {"latencies": latencies, "validation_errors": validation_errors, "total_runs": runs}


def bench_xcp(host: str, port: int, payload: bytes, payload_checksum: str, runs: int, codec: str) -> dict:
    if Server is None:
        raise RuntimeError("xcp reference library is missing. Install before running benchmark.")
    from xcp import Client

    latencies = []
    validation_errors = 0

    client = Client(host, port)
    try:
        for run_num in tqdm(range(runs), desc=f"XCP v0.2 + {codec.upper()}"):
            try:
                # Create Ether envelope with the payload
                # For binary data, we need to encode it as a string
                if codec == "json":
                    payload_data = payload.hex()
                else:  # binary
                    payload_data = payload.hex()  # Use hex for consistency

                ether = Ether(
                    kind="benchmark",
                    schema_version=1,
                    payload={"data": payload_data, "run_id": run_num, "codec": codec},
                    metadata={"timestamp": time.time(), "uuid": str(uuid.uuid4())},
                )

                # Choose codec ID based on codec string
                codec_id = 0x01 if codec == "json" else 0x08  # JSON or Protobuf

                start = time.perf_counter()
                response = client.send_ether(ether, codec_id=codec_id)
                elapsed = time.perf_counter() - start
                latencies.append(elapsed)

                # Extract payload from response
                response_payload = None
                if codec == "json":
                    try:
                        response_data = json.loads(response.payload.decode("utf-8"))
                        response_ether = Ether(**response_data)
                        response_payload = bytes.fromhex(response_ether.payload["data"])
                    except (json.JSONDecodeError, KeyError, ValueError):
                        print(f"❌ Run {run_num}: Invalid JSON response")
                        validation_errors += 1
                        continue
                else:  # binary/protobuf
                    # For Protobuf, the response is the encoded Ether envelope
                    try:
                        from xcp.codecs import get_codec

                        protobuf_codec = get_codec(0x08)
                        response_ether = protobuf_codec.decode(response.payload)
                        if isinstance(response_ether, Ether):
                            response_payload = bytes.fromhex(response_ether.payload["data"])
                        else:
                            response_payload = response.payload
                    except Exception as e:
                        print(f"❌ Run {run_num}: Invalid Protobuf response: {e}")
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
    latencies: list[float], size_bytes: int, validation_errors: int, total_runs: int, unit: str = "us"
) -> dict[str, float]:
    if not latencies:
        return {"p50": float("nan"), "p95": float("nan"), "p99": float("nan"), "thr": 0.0, "success_rate": 0.0}
    lat_us = [t * 1e6 for t in latencies]
    p50, p95, p99 = quantiles(lat_us, n=100)[:3]
    throughput = (size_bytes * len(latencies)) / sum(latencies) / (2**20)  # MiB/s
    success_rate = (total_runs - validation_errors) / total_runs * 100
    return {"p50": p50, "p95": p95, "p99": p99, "thr": throughput, "success_rate": success_rate}


def print_table(results: dict[str, dict[str, float]], unit: str = "us"):
    console = Console()
    table = Table(title="Multi-Codec Benchmark Results", box=box.SIMPLE_HEAVY)
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


def print_validation_summary(results: dict[str, dict]):
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    for label, res in results.items():
        errors = res.get("validation_errors", 0)
        total = res.get("total_runs", 0)
        success = total - errors
        if total == 0:
            percent = 0.0
        else:
            percent = success / total * 100
        print(f"{label:25}: {success}/{total} successful ({percent:.1f}%)")
    if any(res.get("validation_errors", 0) > 0 for res in results.values()):
        print("\n⚠️  WARNING: Data integrity issues detected!")
        print("   This may indicate protocol implementation problems.")
        print("   Check the error messages above for details.")
    else:
        print("\n✅ All tests passed validation - no data loss detected!")


def main():
    parser = argparse.ArgumentParser(description="Multi-codec benchmark: HTTP/2 vs XCP v0.2")
    parser.add_argument("--runs", type=int, default=100, help="Number of benchmark runs")
    parser.add_argument("--size", type=int, default=1024, help="Payload size in bytes")
    parser.add_argument("--unit", choices=["us", "ms", "ns"], default="us", help="Latency unit")
    args = parser.parse_args()

    # Generate payloads
    json_payload, json_checksum = generate_json_payload(args.size)
    f16_payload, f16_checksum = generate_f16_payload(args.size)

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
        results = {}

        # HTTP/2 benchmarks
        http_json_results = bench_http2(
            f"http://127.0.0.1:{http_port}/", json_payload, json_checksum, args.runs, "json"
        )
        results["HTTP/2 + JSON"] = summarise(
            http_json_results["latencies"],
            args.size,
            http_json_results["validation_errors"],
            http_json_results["total_runs"],
            args.unit,
        )

        http_binary_results = bench_http2(
            f"http://127.0.0.1:{http_port}/", f16_payload, f16_checksum, args.runs, "binary"
        )
        results["HTTP/2 + Binary"] = summarise(
            http_binary_results["latencies"],
            args.size,
            http_binary_results["validation_errors"],
            http_binary_results["total_runs"],
            args.unit,
        )

        # XCP v0.2 benchmarks
        xcp_json_results = bench_xcp("127.0.0.1", xcp_port, json_payload, json_checksum, args.runs, "json")
        results["XCP v0.2 + JSON"] = summarise(
            xcp_json_results["latencies"],
            args.size,
            xcp_json_results["validation_errors"],
            xcp_json_results["total_runs"],
            args.unit,
        )

        xcp_binary_results = bench_xcp("127.0.0.1", xcp_port, f16_payload, f16_checksum, args.runs, "binary")
        results["XCP v0.2 + Binary"] = summarise(
            xcp_binary_results["latencies"],
            args.size,
            xcp_binary_results["validation_errors"],
            xcp_binary_results["total_runs"],
            args.unit,
        )

        # Print results
        print_table(results, args.unit)
        print_validation_summary(
            {
                "HTTP/2 + JSON": http_json_results,
                "HTTP/2 + Binary": http_binary_results,
                "XCP v0.2 + JSON": xcp_json_results,
                "XCP v0.2 + Binary": xcp_binary_results,
            }
        )

    finally:
        # Cleanup
        pass  # Threads are daemon, will exit when main thread exits


if __name__ == "__main__":
    main()
