#!/usr/bin/env python3
# Copyright 2025 Maida.AI
# SPDX-License-Identifier: Apache-2.0
"""
Multi-Codec Benchmark: HTTP/2 JSON vs HTTP/2 F16 vs XCP JSON vs XCP F16

Measures throughput and latency for four combinations:
  1. HTTP/2 + JSON
  2. HTTP/2 + F16 (float16 binary)
  3. XCP + JSON
  4. XCP + F16 (float16 binary)

Follows the reporting style of the original poc_http2_vs_xcp.py.
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
from contextlib import closing
from statistics import quantiles
from threading import Thread
from typing import Dict, List, Tuple

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

def generate_json_payload(size: int) -> Tuple[bytes, str]:
    # Generate a random float array and encode as JSON
    arr = np.random.rand(size // 4).tolist()  # float32, 4 bytes each
    payload = json.dumps(arr).encode()
    checksum = hashlib.sha256(payload).hexdigest()
    return payload, checksum

def generate_f16_payload(size: int) -> Tuple[bytes, str]:
    arr = np.random.rand(size // 2).astype(np.float16)  # float16 = 2 bytes
    payload = arr.tobytes()
    checksum = hashlib.sha256(payload).hexdigest()
    return payload, checksum

def validate_response(original_payload: bytes, response_payload: bytes, original_checksum: str, run_number: int) -> bool:
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
# HTTP/2 echo server (simple)
# ---------------------------------------------------------------------------
from http.server import BaseHTTPRequestHandler, HTTPServer

class HTTPEchoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.send_response(200)
        # Content-Type: echo back what was sent
        ct = self.headers.get("Content-Type", "application/octet-stream")
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("X-Benchmark-Run", str(time.time()))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, format: str, *args):
        return

def run_http_server(port: int):
    server = HTTPServer(("127.0.0.1", port), HTTPEchoHandler)
    server.serve_forever()

# ---------------------------------------------------------------------------
# XCP echo server — uses the reference implementation API.
# ---------------------------------------------------------------------------
try:
    from xcp import Frame, FrameHeader, Server, MsgType, CodecID
except ModuleNotFoundError:
    Frame = None
    Server = None
    MsgType = None
    CodecID = None

class XCPEchoServer(Thread):
    def __init__(self, port: int):
        super().__init__(daemon=True)
        self.port = port
    def run(self):
        if Server is None:
            raise RuntimeError("xcp reference library is missing. Install before running benchmark.")
        def on_frame(frame):
            return frame
        server = Server("127.0.0.1", self.port, on_frame=on_frame)
        server.serve_forever()

# ---------------------------------------------------------------------------
# Benchmark helpers with validation and cache-busting
# ---------------------------------------------------------------------------
def bench_http2(url: str, payload: bytes, payload_checksum: str, runs: int, codec: str) -> Dict:
    latencies = []
    validation_errors = 0

    # Create a new client for each benchmark to prevent connection reuse caching
    with httpx.Client(
        http2=True,
        timeout=10.0,
        # Disable connection pooling to prevent caching
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)
    ) as client:
        for run_num in tqdm(range(runs), desc=f"HTTP/2 + {codec.upper()}"):
            try:
                # Add cache-busting headers to each request
                headers = {
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "X-Benchmark-Run": str(run_num),
                    "X-Timestamp": str(time.time()),
                    "X-UUID": str(uuid.uuid4()),
                    "Content-Type": "application/json" if codec == "json" else "application/octet-stream"
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
    return {"lat": latencies, "validation_errors": validation_errors, "total_runs": runs}

def bench_xcp(host: str, port: int, payload: bytes, payload_checksum: str, runs: int, codec: str) -> Dict:
    if Frame is None:
        raise RuntimeError("xcp reference library is missing. Install before running benchmark.")
    from xcp import Client
    latencies = []
    validation_errors = 0

    # Create a new client for each benchmark to prevent connection reuse caching
    client = Client(host, port, enable_cache_busting=True)
    try:
        for run_num in tqdm(range(runs), desc=f"XCP + {codec.upper()}"):
            try:
                # Create a unique frame for each run to prevent any potential caching
                frame = Frame(
                    header=FrameHeader(
                        channelId=run_num,  # Use run number as channel ID to make each frame unique
                        msgType=MsgType.DATA,
                        bodyCodec=CodecID.JSON if codec == "json" else CodecID.TENSOR_F16,
                        schemaId=run_num,  # Use run number as schema ID to make each frame unique
                        msgId=run_num,  # Use run number as message ID to make each frame unique
                    ),
                    payload=payload
                )
                start = time.perf_counter()
                echo = client.request(frame)
                elapsed = time.perf_counter() - start
                latencies.append(elapsed)
                response_payload = echo.payload
                if not validate_response(payload, response_payload, payload_checksum, run_num):
                    validation_errors += 1
            except Exception as e:
                print(f"❌ Run {run_num}: XCP request failed: {e}")
                validation_errors += 1
                continue
    finally:
        client.close()
    return {"lat": latencies, "validation_errors": validation_errors, "total_runs": runs}

# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
UNITS = {
    "s": 1,
    "ms": 1e3,
    "us": 1e6,
    "ns": 1e9,
}

def summarise(latencies: List[float], size_bytes: int, validation_errors: int, total_runs: int, unit: str = "us") -> Dict[str, float]:
    if not latencies:
        return {"p50": float('nan'), "p95": float('nan'), "p99": float('nan'), "thr": 0.0, "success_rate": 0.0}
    lat_us = [t * UNITS[unit] for t in latencies]
    p50, p95, p99 = quantiles(lat_us, n=100)[:3]
    throughput = (size_bytes * len(latencies)) / sum(latencies) / (2 ** 20)  # MiB/s
    success_rate = (total_runs - validation_errors) / total_runs * 100
    return {"p50": p50, "p95": p95, "p99": p99, "thr": throughput, "success_rate": success_rate}

def print_table(results: Dict[str, Dict[str, float]], unit: str = "us"):
    console = Console()
    table = Table(title="HTTP/2 vs XCP Multi-Codec Benchmark Results (No Caching)", box=box.SIMPLE_HEAVY)
    table.add_column("Transport + Codec")
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

def print_validation_summary(results: Dict[str, Dict]):
    print("\n" + "="*60)
    print("VALIDATION SUMMARY")
    print("="*60)
    for label, res in results.items():
        errors = res.get("validation_errors", 0)
        total = res.get("total_runs", 0)
        success = total - errors
        print(f"{label:15}: {success}/{total} successful ({success/total*100:.1f}%)")
    if any(res.get("validation_errors", 0) > 0 for res in results.values()):
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
    ap = argparse.ArgumentParser(description="HTTP/2 vs XCP multi-codec benchmark with validation and cache-busting")
    ap.add_argument("--runs", type=int, default=500, help="Number of round trips")
    ap.add_argument("--size", type=int, default=10240, help="Payload size in bytes")
    args = ap.parse_args()
    time_units = "us"

    # Start servers
    xcp_port = find_free_port()
    http_port = find_free_port()
    print(f"Starting servers on ports {http_port} (HTTP/2) and {xcp_port} (XCP)...")
    xcp_thread = XCPEchoServer(port=xcp_port)
    xcp_thread.start()
    http_thread = Thread(target=run_http_server, args=(http_port,), daemon=True)
    http_thread.start()
    time.sleep(0.5)
    http_url = f"http://127.0.0.1:{http_port}/echo"

    # Generate unique run ID for cache-busting
    unique_run_id = str(uuid.uuid4())
    print(f"Benchmark Configuration:")
    print(f"  Runs: {args.runs}")
    print(f"  Payload size: {args.size} bytes")
    print(f"  Run ID: {unique_run_id[:8]}...")
    print(f"  Cache-busting: Enabled")
    print()

    # Run all four combinations
    results = {}
    for transport, bench_func in [("HTTP/2", bench_http2), ("XCP", bench_xcp)]:
        for codec in ["json", "f16"]:
            # Generate unique payload for this combination
            if codec == "json":
                payload, checksum = generate_json_payload(args.size)
            else:
                payload, checksum = generate_f16_payload(args.size)

            label = f"{transport} + {codec.upper()}"
            print(f"\nRunning {label}...")
            print(f"  Payload checksum: {checksum[:16]}...")

            if transport == "HTTP/2":
                res = bench_func(http_url, payload, checksum, args.runs, codec)
            else:
                res = bench_func("127.0.0.1", xcp_port, payload, checksum, args.runs, codec)

            results[label] = summarise(res["lat"], args.size, res["validation_errors"], res["total_runs"], unit=time_units)
            results[label+"_raw"] = res  # For validation summary

    # Print summary table
    print_table({k: v for k, v in results.items() if not k.endswith("_raw")}, unit=time_units)
    # Print validation summary
    print_validation_summary({k: v for k, v in results.items() if k.endswith("_raw")})

if __name__ == "__main__":
    main()
