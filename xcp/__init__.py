r"""Minimal proof-of-concept implementation of **XCP** (eXtensible Coordination
Protocol) sufficient for local echo benchmarks.

This implementation follows the XCP v0.1 specification and provides:
- Binary frame format with proper headers
- Client/Server classes for the benchmark
- Frame and FrameHeader classes
- Support for JSON and binary codecs
- Basic handshake protocol
"""
from __future__ import annotations

import json
import logging
import socket
import struct
import threading
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional, Callable, Any
from contextlib import contextmanager

# ----------------------------------------------------------------------------
# Constants & enums
# ----------------------------------------------------------------------------

MAGIC = 0xA9A17A10
MAJOR = 0x0
MINOR = 0x1
VERSION_BYTE = (MAJOR << 4) | MINOR

class Flag(IntEnum):
    COMP = 0b1000_0000
    CRYPT = 0b0100_0000
    MORE = 0b0010_0000
    LARGE = 0b0001_0000

class CodecID(IntEnum):
    JSON = 0x01
    TENSOR_F32 = 0x02
    TENSOR_F16 = 0x03
    TENSOR_QNT8 = 0x04
    BINARY = 0x10  # raw bytes, no interpretation

class MsgType(IntEnum):
    HELLO = 0x01
    CAPS_ACK = 0x02
    PING = 0x03
    PONG = 0x04
    NEGOTIATE = 0x05
    UNSUPPORTED = 0x06
    ACK = 0x07
    NACK = 0x08
    DATA = 0x20

# ----------------------------------------------------------------------------
# Frame classes
# ----------------------------------------------------------------------------

@dataclass
class FrameHeader:
    channelId: int = 0
    msgType: int = MsgType.DATA
    bodyCodec: int = CodecID.JSON
    schemaId: int = 0
    msgId: int = 0
    inReplyTo: int = 0
    tags: list = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []

    def to_dict(self) -> dict:
        return {
            "channelId": self.channelId,
            "msgType": self.msgType,
            "bodyCodec": self.bodyCodec,
            "schemaId": self.schemaId,
            "msgId": self.msgId,
            "inReplyTo": self.inReplyTo,
            "tags": self.tags
        }

    @classmethod
    def from_dict(cls, data: dict) -> FrameHeader:
        return cls(
            channelId=data.get("channelId", 0),
            msgType=data.get("msgType", MsgType.DATA),
            bodyCodec=data.get("bodyCodec", CodecID.JSON),
            schemaId=data.get("schemaId", 0),
            msgId=data.get("msgId", 0),
            inReplyTo=data.get("inReplyTo", 0),
            tags=data.get("tags", [])
        )

@dataclass
class Frame:
    header: FrameHeader
    payload: bytes

    def __post_init__(self):
        if isinstance(self.payload, str):
            self.payload = self.payload.encode()

# ----------------------------------------------------------------------------
# Frame serialization/deserialization
# ----------------------------------------------------------------------------

def pack_frame(frame: Frame, flags: int = 0) -> bytes:
    """Pack a Frame into the XCP binary format."""
    header_dict = frame.header.to_dict()
    header_bytes = json.dumps(header_dict, separators=(",", ":")).encode()
    hlen = len(header_bytes)

    # decide LARGE flag & PLEN field size
    large = len(frame.payload) >= 2 ** 32
    if large:
        flags |= Flag.LARGE
        plen_field = struct.pack("!Q", len(frame.payload))  # 8-byte
    else:
        plen_field = struct.pack("!I", len(frame.payload))

    prefix = struct.pack("!I B B H", MAGIC, VERSION_BYTE, flags, hlen)
    return prefix + header_bytes + plen_field + frame.payload

def recv_exact(sock: socket.socket, n: int) -> bytes:
    """Receive exactly n bytes from socket."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Unexpected EOF from peer")
        buf.extend(chunk)
    return bytes(buf)

def parse_frame(sock: socket.socket) -> Frame:
    """Parse an XCP frame from socket."""
    # read first fixed 8 bytes
    pre = recv_exact(sock, 8)
    magic, ver, flags, hlen = struct.unpack("!I B B H", pre)
    if magic != MAGIC:
        raise ValueError("Bad MAGIC header")

    # read header
    header_raw = recv_exact(sock, hlen)
    header_dict = json.loads(header_raw.decode())
    header = FrameHeader.from_dict(header_dict)

    # determine PLEN length
    if flags & Flag.LARGE:
        plen_bytes = recv_exact(sock, 8)
        payload_len = struct.unpack("!Q", plen_bytes)[0]
    else:
        plen_bytes = recv_exact(sock, 4)
        payload_len = struct.unpack("!I", plen_bytes)[0]

    payload = recv_exact(sock, payload_len)
    return Frame(header=header, payload=payload)

# ----------------------------------------------------------------------------
# Client implementation
# ----------------------------------------------------------------------------

class Client:
    """XCP client for sending frames to a server."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9944):
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._msg_id = 1
        self._lock = threading.Lock()
        self._connect()

    def _connect(self):
        """Establish connection and perform handshake."""
        self._sock = socket.create_connection((self.host, self.port), timeout=5.0)

        # Send HELLO
        hello_header = FrameHeader(
            msgType=MsgType.HELLO,
            bodyCodec=CodecID.JSON,
            msgId=self._msg_id
        )
        hello_frame = Frame(header=hello_header, payload=b"")
        self._sock.sendall(pack_frame(hello_frame))

        # Expect CAPS_ACK
        response = parse_frame(self._sock)
        if response.header.msgType != MsgType.CAPS_ACK:
            raise RuntimeError("Handshake failed: expected CAPS_ACK")

    def request(self, frame: Frame) -> Frame:
        """Send a frame and wait for response."""
        with self._lock:
            frame.header.msgId = self._msg_id
            self._msg_id += 1

        self._sock.sendall(pack_frame(frame))
        response = parse_frame(self._sock)
        return response

    def close(self):
        """Close the connection."""
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            finally:
                self._sock.close()

# ----------------------------------------------------------------------------
# Server implementation
# ----------------------------------------------------------------------------

class _ClientHandler(threading.Thread):
    """Handle a single client connection."""

    def __init__(self, sock: socket.socket, addr, on_frame: Callable[[Frame], Frame]):
        super().__init__(daemon=True)
        self.sock = sock
        self.addr = addr
        self.on_frame = on_frame
        self.running = True

    def run(self):
        try:
            self._serve()
        except Exception as exc:
            logging.debug("Client %s closed: %s", self.addr, exc)
        finally:
            self.sock.close()

    def _serve(self):
        # Expect HELLO
        frame = parse_frame(self.sock)
        if frame.header.msgType != MsgType.HELLO:
            return

        # Send CAPS_ACK
        ack_header = FrameHeader(
            msgType=MsgType.CAPS_ACK,
            bodyCodec=CodecID.JSON,
            inReplyTo=frame.header.msgId
        )
        ack_frame = Frame(header=ack_header, payload=b"")
        self.sock.sendall(pack_frame(ack_frame))

        # Handle frames
        while self.running:
            try:
                frame = parse_frame(self.sock)
                response = self.on_frame(frame)
                if response:
                    self.sock.sendall(pack_frame(response))
            except (ConnectionError, ValueError):
                break

class Server:
    """XCP server that accepts connections and handles frames."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9944,
                 on_frame: Callable[[Frame], Frame] = None):
        self.host = host
        self.port = port
        self.on_frame = on_frame or self._default_handler
        self._sock: Optional[socket.socket] = None
        self._running = threading.Event()

    def _default_handler(self, frame: Frame) -> Frame:
        """Default handler that echoes DATA frames."""
        if frame.header.msgType == MsgType.DATA:
            # Echo the frame back
            response_header = FrameHeader(
                channelId=frame.header.channelId,
                msgType=frame.header.msgType,
                bodyCodec=frame.header.bodyCodec,
                schemaId=frame.header.schemaId,
                inReplyTo=frame.header.msgId
            )
            return Frame(header=response_header, payload=frame.payload)
        return None

    def serve_forever(self):
        """Start the server and handle connections."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self.host, self.port))
            srv.listen()
            self._sock = srv
            self._running.set()

            while self._running.is_set():
                try:
                    cli_sock, addr = srv.accept()
                    _ClientHandler(cli_sock, addr, self.on_frame).start()
                except OSError:
                    break  # socket closed

    def stop(self):
        """Stop the server."""
        self._running.clear()
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

# ----------------------------------------------------------------------------
# Legacy API compatibility (for existing code)
# ----------------------------------------------------------------------------

class XCPConnection:
    """Legacy client connection API for backward compatibility."""

    def __init__(self, sock: socket.socket):
        self._client = Client()
        self._client._sock = sock
        self._client._msg_id = 1

    def send(self, payload: bytes | str, codec: CodecID = CodecID.JSON) -> bytes:
        """Send payload and wait for echo response."""
        if isinstance(payload, str):
            payload_bytes = payload.encode()
        else:
            payload_bytes = payload

        header = FrameHeader(
            msgType=MsgType.DATA,
            bodyCodec=int(codec)
        )
        frame = Frame(header=header, payload=payload_bytes)
        response = self._client.request(frame)
        return response.payload

    def close(self):
        self._client.close()

def open(host: str = "127.0.0.1", port: int = 9944, timeout: float = 5.0) -> XCPConnection:
    """Legacy function to open a client connection."""
    s = socket.create_connection((host, port), timeout=timeout)
    return XCPConnection(s)

class EchoServer(threading.Thread):
    """Legacy echo server for backward compatibility."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9944):
        super().__init__(name="xcp-echo-server")
        self.host = host
        self.port = port
        self._server = Server(host, port)
        self._running = threading.Event()

    def run(self):
        self._running.set()
        self._server.serve_forever()

    def stop(self):
        self._running.clear()
        self._server.stop()

@contextmanager
def run_echo_server(host: str = "127.0.0.1", port: int = 9944):
    """Context manager to spin up an echo server in a background thread."""
    srv = EchoServer(host, port)
    srv.start()
    # wait until listening
    while not srv._running.is_set():
        time.sleep(0.01)
    try:
        yield
    finally:
        srv.stop()
        srv.join()

# ----------------------------------------------------------------------------
# Exports
# ----------------------------------------------------------------------------

__all__ = [
    "Frame",
    "FrameHeader",
    "Client",
    "Server",
    "open",
    "run_echo_server",
    "CodecID",
    "MsgType",
    "Flag",
    "pack_frame",
    "parse_frame",
]
