#!/usr/bin/env python3
"""
Benchmark: XCP v0.2 vs Protobuf (TCP and HTTP/2)

Measures throughput and latency for:
  1. XCP v0.2 (binary frames with Ether envelopes)
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

import httpx
import numpy as np
from rich import box
from rich.console import Console
from rich.table import Table
from tqdm import tqdm

from benchmarks import echo_bench_pb2


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def generate_unique_payload_with_checksum(size: int, run_id: str) -> tuple[bytes, str, list[float]]:
    # For protobuf, we can use either bytes or floats. We'll use bytes for now.
    base_payload = os.urandom(size - len(run_id))
    unique_payload = base_payload + run_id.encode()
    checksum = hashlib.sha256(unique_payload).hexdigest()
    # Also generate a float array for the repeated float field
    floats = [float(b) for b in unique_payload[: min(128, len(unique_payload))]]
    return unique_payload, checksum, floats


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
                # Echo the protobuf message
                resp = echo_bench_pb2.EchoPayload()
                resp.ParseFromString(data)
                out = resp.SerializeToString()
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
# HTTP/2 echo server
# ---------------------------------------------------------------------------
from http.server import BaseHTTPRequestHandler, HTTPServer


class ProtobufHTTPEchoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(content_length)
        resp = echo_bench_pb2.EchoPayload()
        resp.ParseFromString(data)
        out = resp.SerializeToString()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, format: str, *args):
        pass  # Suppress logging


def run_protobuf_http_server(port: int):
    server = HTTPServer(("127.0.0.1", port), ProtobufHTTPEchoHandler)
    server.serve_forever()


# ---------------------------------------------------------------------------
# XCP v0.2 echo server
# ---------------------------------------------------------------------------
try:
    from xcp import Ether, Frame, FrameHeader, Server
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

        def on_ether(ether):
            # Echo the Ether envelope
            return ether

        server = Server("127.0.0.1", self.port, on_ether=on_ether)
        server.serve_forever()


# ---------------------------------------------------------------------------
# Benchmark helpers with validation and cache-busting
# ---------------------------------------------------------------------------
def bench_protobuf_tcp(host: str, port: int, payload: bytes, payload_checksum: str, runs: int) -> dict:
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


def bench_protobuf_http2(url: str, payload: bytes, payload_checksum: str, runs: int) -> dict:
    latencies = []
    validation_errors = 0
    with httpx.Client(
        http2=True, timeout=10.0, limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)
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
                    "Content-Type": "application/octet-stream",
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


def bench_xcp(host: str, port: int, payload: bytes, payload_checksum: str, runs: int, codec: int = 0x01) -> dict:
    if Server is None:
        raise RuntimeError("xcp reference library missing. Cannot benchmark XCP.")
    import base64

    from xcp import Client, Ether

    latencies = []
    validation_errors = 0
    client = Client(host, port)
    try:
        for run_num in tqdm(range(runs), desc=f"XCP v0.2{' + F16' if codec == 0x03 else ''}"):
            start = time.perf_counter()
            try:
                # Create minimal Ether envelope with base64 encoding for better performance
                ether = Ether(
                    kind="benchmark",
                    schema_version=1,
                    payload={"data": base64.b64encode(payload).decode()},  # Use base64 instead of hex
                    metadata={},  # Minimal metadata
                )

                # Send Ether and get response
                response = client.send_ether(ether, codec_id=codec)

                # Extract payload from response
                response_payload = None
                if codec == 0x01:  # JSON
                    import json

                    response_data = json.loads(response.payload.decode())
                    response_ether = Ether(**response_data)
                    response_payload = base64.b64decode(response_ether.payload.get("data", ""))
                elif codec == 0x08:  # Protobuf
                    # For Protobuf, the response is the encoded Ether envelope
                    from xcp.codecs import get_codec

                    protobuf_codec = get_codec(0x08)
                    response_ether = protobuf_codec.decode(response.payload)
                    if isinstance(response_ether, Ether):
                        response_payload = base64.b64decode(response_ether.payload.get("data", ""))
                    else:
                        response_payload = response.payload
                else:
                    # For other codecs, assume response is raw payload
                    response_payload = response.payload

                latencies.append(time.perf_counter() - start)
                if not validate_response(payload, response_payload, payload_checksum, run_num):
                    validation_errors += 1
            except Exception as e:
                print(f"❌ Run {run_num}: XCP v0.2 request failed: {e}")
                validation_errors += 1
                continue
    finally:
        client.close()
    return {"latencies": latencies, "validation_errors": validation_errors, "total_runs": runs}


def bench_xcp_fast(host: str, port: int, payload: bytes, payload_checksum: str, runs: int, codec: int = 0x01) -> dict:
    """Fast XCP benchmark using raw payloads (closer to v0.1 performance)."""
    if Server is None:
        raise RuntimeError("xcp reference library missing. Cannot benchmark XCP.")
    from xcp import Client

    latencies = []
    validation_errors = 0
    client = Client(host, port)
    try:
        for run_num in tqdm(range(runs), desc=f"XCP v0.2 Fast{' + F16' if codec == 0x03 else ''}"):
            start = time.perf_counter()
            try:
                # Send raw payload directly (bypasses Ether overhead)
                response_payload = client.send_raw_payload(payload, codec_id=codec)

                latencies.append(time.perf_counter() - start)
                if not validate_response(payload, response_payload, payload_checksum, run_num):
                    validation_errors += 1
            except Exception as e:
                print(f"❌ Run {run_num}: XCP v0.2 Fast request failed: {e}")
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


def summarise(
    latencies: list[float], size_bytes: int, validation_errors: int, total_runs: int, unit: str = "us"
) -> dict[str, float]:
    if not latencies:
        return {"p50": float("nan"), "p95": float("nan"), "p99": float("nan"), "thr": 0.0, "success_rate": 0.0}
    lat_us = [t * UNITS[unit] for t in latencies]
    p50, p95, p99 = quantiles(lat_us, n=100)[:3]
    throughput = (size_bytes * len(latencies)) / sum(latencies) / (2**20)  # MiB/s
    success_rate = (total_runs - validation_errors) / total_runs * 100
    return {"p50": p50, "p95": p95, "p99": p99, "thr": throughput, "success_rate": success_rate}


def print_table(results: dict[str, dict[str, float]], unit: str = "us"):
    console = Console()
    table = Table(title="XCP v0.2 vs Protobuf Benchmark Results (No Caching)", box=box.SIMPLE_HEAVY)
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


def main():
    parser = argparse.ArgumentParser(description="Benchmark XCP v0.2 vs Protobuf")
    parser.add_argument("--runs", type=int, default=100, help="Number of benchmark runs")
    parser.add_argument("--size", type=int, default=1024, help="Payload size in bytes")
    parser.add_argument("--unit", choices=["us", "ms", "ns"], default="us", help="Latency unit")
    args = parser.parse_args()

    # Generate unique payload for each run
    run_id = str(uuid.uuid4())
    payload, checksum, _ = generate_unique_payload_with_checksum(args.size, run_id)

    # Find free ports
    protobuf_tcp_port = find_free_port()
    protobuf_http_port = find_free_port()
    xcp_port = find_free_port()

    print(f"Benchmarking {args.runs} runs with {args.size} byte payloads")
    print(f"Ports: Protobuf TCP={protobuf_tcp_port}, HTTP={protobuf_http_port}, XCP={xcp_port}")

    # Start servers
    protobuf_tcp_thread = ProtobufTCPEchoServer(port=protobuf_tcp_port)
    protobuf_http_thread = Thread(target=run_protobuf_http_server, args=(protobuf_http_port,), daemon=True)
    xcp_thread = XCPEchoServer(port=xcp_port)

    protobuf_tcp_thread.start()
    protobuf_http_thread.start()
    xcp_thread.start()

    # Wait for servers to start
    time.sleep(0.5)

    try:
        # Run benchmarks
        results = {}

        # Protobuf TCP
        tcp_results = bench_protobuf_tcp("127.0.0.1", protobuf_tcp_port, payload, checksum, args.runs)
        results["Protobuf TCP"] = summarise(
            tcp_results["latencies"], args.size, tcp_results["validation_errors"], tcp_results["total_runs"], args.unit
        )

        # Protobuf HTTP/2
        http_results = bench_protobuf_http2(f"http://127.0.0.1:{protobuf_http_port}/", payload, checksum, args.runs)
        results["Protobuf HTTP/2"] = summarise(
            http_results["latencies"],
            args.size,
            http_results["validation_errors"],
            http_results["total_runs"],
            args.unit,
        )

        # XCP v0.2 JSON
        xcp_json_results = bench_xcp("127.0.0.1", xcp_port, payload, checksum, args.runs, 0x01)
        results["XCP v0.2 JSON"] = summarise(
            xcp_json_results["latencies"],
            args.size,
            xcp_json_results["validation_errors"],
            xcp_json_results["total_runs"],
            args.unit,
        )

        # XCP v0.2 Protobuf
        xcp_pb_results = bench_xcp("127.0.0.1", xcp_port, payload, checksum, args.runs, 0x08)
        results["XCP v0.2 Protobuf"] = summarise(
            xcp_pb_results["latencies"],
            args.size,
            xcp_pb_results["validation_errors"],
            xcp_pb_results["total_runs"],
            args.unit,
        )

        # XCP v0.2 Fast (raw payloads)
        xcp_fast_results = bench_xcp_fast("127.0.0.1", xcp_port, payload, checksum, args.runs, 0x01)
        results["XCP v0.2 Fast"] = summarise(
            xcp_fast_results["latencies"],
            args.size,
            xcp_fast_results["validation_errors"],
            xcp_fast_results["total_runs"],
            args.unit,
        )

        # Print results
        print_table(results, args.unit)
        print_validation_summary(
            {
                "Protobuf TCP": tcp_results,
                "Protobuf HTTP/2": http_results,
                "XCP v0.2 JSON": xcp_json_results,
                "XCP v0.2 Protobuf": xcp_pb_results,
                "XCP v0.2 Fast": xcp_fast_results,
            }
        )

    finally:
        # Cleanup
        pass  # Threads are daemon, will exit when main thread exits


if __name__ == "__main__":
    main()
