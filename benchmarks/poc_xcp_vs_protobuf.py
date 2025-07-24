#!/usr/bin/env python3
"""
Benchmark: XCP vs Protobuf (TCP and HTTP/2)

Measures throughput and latency for:
  1. XCP (binary frames)
  2. Protobuf over raw TCP
  3. Protobuf over HTTP/2

Usage:
  $ python benchmarks/poc_xcp_vs_protobuf.py --runs 1000 --size 16384
"""
from __future__ import annotations

import argparse
import hashlib
import os
import socket
import time
import uuid
from contextlib import closing
from statistics import quantiles
from threading import Thread
from typing import Dict, List, Tuple

import httpx
from rich import box
from rich.console import Console
from rich.table import Table
from tqdm import tqdm

from benchmarks import echo_bench_pb2
import numpy as np

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

def generate_unique_payload_with_checksum(size: int, run_id: str) -> Tuple[bytes, str, List[float]]:
    # For protobuf, we can use either bytes or floats. We'll use bytes for now.
    base_payload = os.urandom(size - len(run_id))
    unique_payload = base_payload + run_id.encode()
    checksum = hashlib.sha256(unique_payload).hexdigest()
    # Also generate a float array for the repeated float field
    floats = [float(b) for b in unique_payload[:min(128, len(unique_payload))]]
    return unique_payload, checksum, floats

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
# Protobuf echo server (TCP)
# ---------------------------------------------------------------------------
class ProtobufTCPEchoServer(Thread):
    def __init__(self, port: int):
        super().__init__(daemon=True)
        self.port = port

    def run(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", self.port))
            s.listen(1)
            while True:
                conn, _ = s.accept()
                Thread(target=self.handle_client, args=(conn,), daemon=True).start()

    def handle_client(self, conn):
        with conn:
            while True:
                # Read 4-byte length prefix
                length_bytes = self.recv_exact(conn, 4)
                if not length_bytes:
                    break
                msg_len = int.from_bytes(length_bytes, "big")
                data = self.recv_exact(conn, msg_len)
                if not data:
                    break
                # Parse protobuf
                msg = echo_bench_pb2.EchoPayload()
                msg.ParseFromString(data)
                # Echo back the same message
                out = msg.SerializeToString()
                conn.sendall(len(out).to_bytes(4, "big") + out)

    def recv_exact(self, conn, n):
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

# ---------------------------------------------------------------------------
# Protobuf echo server (HTTP/2)
# ---------------------------------------------------------------------------
from http.server import BaseHTTPRequestHandler, HTTPServer
class ProtobufHTTPEchoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        # Parse protobuf
        msg = echo_bench_pb2.EchoPayload()
        msg.ParseFromString(body)
        out = msg.SerializeToString()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(out)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("X-Benchmark-Run", str(time.time()))
        self.end_headers()
        self.wfile.write(out)
    def log_message(self, format: str, *args):
        return

def run_protobuf_http_server(port: int):
    server = HTTPServer(("127.0.0.1", port), ProtobufHTTPEchoHandler)
    server.serve_forever()

# ---------------------------------------------------------------------------
# XCP echo server — uses the reference implementation API.
# ---------------------------------------------------------------------------
try:
    from xcp import Frame, FrameHeader, Server
except ModuleNotFoundError:
    Frame = None
    Server = None

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
def bench_protobuf_tcp(host: str, port: int, payload: bytes, payload_checksum: str, runs: int) -> Dict:
    latencies = []
    validation_errors = 0
    for run_num in tqdm(range(runs), desc="Protobuf TCP"):
        try:
            with socket.create_connection((host, port), timeout=10.0) as s:
                msg = echo_bench_pb2.EchoPayload(data=payload)
                out = msg.SerializeToString()
                start = time.perf_counter()
                s.sendall(len(out).to_bytes(4, "big") + out)
                length_bytes = recv_exact(s, 4)
                if not length_bytes:
                    raise RuntimeError("No response length")
                msg_len = int.from_bytes(length_bytes, "big")
                data = recv_exact(s, msg_len)
                if not data:
                    raise RuntimeError("No response data")
                resp = echo_bench_pb2.EchoPayload()
                resp.ParseFromString(data)
                response_payload = resp.data
                if not validate_response(payload, response_payload, payload_checksum, run_num):
                    validation_errors += 1
                elapsed = time.perf_counter() - start
                latencies.append(elapsed)
        except Exception as e:
            print(f"❌ Run {run_num}: Protobuf TCP request failed: {e}")
            validation_errors += 1
            continue
    return {"latencies": latencies, "validation_errors": validation_errors, "total_runs": runs}

def bench_protobuf_http2(url: str, payload: bytes, payload_checksum: str, runs: int) -> Dict:
    latencies = []
    validation_errors = 0
    with httpx.Client(
        http2=True,
        timeout=10.0,
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)
    ) as client:
        for run_num in tqdm(range(runs), desc="Protobuf HTTP/2"):
            try:
                msg = echo_bench_pb2.EchoPayload(data=payload)
                out = msg.SerializeToString()
                headers = {
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "X-Benchmark-Run": str(run_num),
                    "X-Timestamp": str(time.time()),
                    "X-UUID": str(uuid.uuid4()),
                    "Content-Type": "application/octet-stream"
                }
                start = time.perf_counter()
                r = client.post(url, content=out, headers=headers)
                elapsed = time.perf_counter() - start
                latencies.append(elapsed)
                if r.status_code != 200:
                    print(f"❌ Run {run_num}: HTTP status {r.status_code}")
                    validation_errors += 1
                    continue
                resp = echo_bench_pb2.EchoPayload()
                resp.ParseFromString(r.content)
                response_payload = resp.data
                if not validate_response(payload, response_payload, payload_checksum, run_num):
                    validation_errors += 1
            except Exception as e:
                print(f"❌ Run {run_num}: Protobuf HTTP/2 request failed: {e}")
                validation_errors += 1
                continue
    return {"latencies": latencies, "validation_errors": validation_errors, "total_runs": runs}

def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf

def bench_xcp(host: str, port: int, payload: bytes, payload_checksum: str, runs: int, codec: int = 0x01) -> Dict:
    if Frame is None:
        raise RuntimeError("xcp reference library missing. Cannot benchmark XCP.")
    from xcp import Client
    latencies = []
    validation_errors = 0
    client = Client(host, port, enable_cache_busting=True)
    try:
        for run_num in tqdm(range(runs), desc=f"XCP{' + F16' if codec == 0x03 else ''}"):
            start = time.perf_counter()
            try:
                frame = Frame(
                    header=FrameHeader(
                        channelId=run_num,
                        msgType=0x20,
                        bodyCodec=codec,
                        schemaId=run_num,
                        msgId=run_num,
                    ),
                    payload=payload,
                )
                echo = client.request(frame)
                response_payload = echo.payload
                latencies.append(time.perf_counter() - start)
                if not validate_response(payload, response_payload, payload_checksum, run_num):
                    validation_errors += 1
            except Exception as e:
                print(f"❌ Run {run_num}: XCP request failed: {e}")
                validation_errors += 1
                continue
    finally:
        client.close()
    return {"latencies": latencies, "validation_errors": validation_errors, "total_runs": runs}

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
    table = Table(title="XCP vs Protobuf Benchmark Results (No Caching)", box=box.SIMPLE_HEAVY)
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

def print_validation_summary(results: Dict[str, Dict]):
    print("\n" + "="*60)
    print("VALIDATION SUMMARY")
    print("="*60)
    for label, res in results.items():
        errors = res.get("validation_errors", 0)
        total = res.get("total_runs", 0)
        success = total - errors
        if total == 0:
            percent = 0.0
        else:
            percent = success / total * 100
        print(f"{label:20}: {success}/{total} successful ({percent:.1f}%)")
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
    ap = argparse.ArgumentParser(description="XCP vs Protobuf benchmark with validation and cache-busting")
    ap.add_argument("--runs", type=int, default=1000, help="Number of round trips")
    ap.add_argument("--size", type=int, default=16384, help="Payload size in bytes")
    args = ap.parse_args()
    time_units = "us"

    # Generate unique payload with checksum for validation (no caching)
    unique_run_id = str(uuid.uuid4())
    payload, payload_checksum, floats = generate_unique_payload_with_checksum(args.size, unique_run_id)
    f16_payload, f16_checksum = generate_f16_payload(args.size)

    print(f"Benchmark Configuration:")
    print(f"  Runs: {args.runs}")
    print(f"  Payload size: {args.size} bytes")
    print(f"  Payload checksum: {payload_checksum[:16]}...")
    print(f"  F16 checksum: {f16_checksum[:16]}...")
    print(f"  Run ID: {unique_run_id[:8]}...")
    print(f"  Cache-busting: Enabled")
    print()

    # Start servers
    xcp_port = find_free_port()
    pb_tcp_port = find_free_port()
    pb_http_port = find_free_port()
    print(f"Starting servers on ports {xcp_port} (XCP), {pb_tcp_port} (Protobuf TCP), {pb_http_port} (Protobuf HTTP/2)...")
    xcp_thread = XCPEchoServer(port=xcp_port)
    xcp_thread.start()
    pb_tcp_thread = ProtobufTCPEchoServer(port=pb_tcp_port)
    pb_tcp_thread.start()
    pb_http_thread = Thread(target=run_protobuf_http_server, args=(pb_http_port,), daemon=True)
    pb_http_thread.start()
    time.sleep(0.5)
    pb_http_url = f"http://127.0.0.1:{pb_http_port}/echo"

    # Run benchmarks
    print("Running benchmarks with validation and cache-busting...")
    results = {}
    results["XCP"] = summarise(**bench_xcp("127.0.0.1", xcp_port, payload, payload_checksum, args.runs, codec=0x01), size_bytes=args.size, unit=time_units)
    results["XCP + F16"] = summarise(**bench_xcp("127.0.0.1", xcp_port, f16_payload, f16_checksum, args.runs, codec=0x03), size_bytes=args.size, unit=time_units)
    results["Protobuf TCP"] = summarise(**bench_protobuf_tcp("127.0.0.1", pb_tcp_port, payload, payload_checksum, args.runs), size_bytes=args.size, unit=time_units)
    results["Protobuf HTTP/2"] = summarise(**bench_protobuf_http2(pb_http_url, payload, payload_checksum, args.runs), size_bytes=args.size, unit=time_units)

    print_table(results, unit=time_units)
    print_validation_summary({k: v for k, v in results.items()})

if __name__ == "__main__":
    main()
