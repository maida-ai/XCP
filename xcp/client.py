# mypy: ignore-errors
"""XCP v0.2 client implementation."""

import socket
import threading
import time
from typing import Any

from .codecs import get_codec, list_codecs
from .constants import DEFAULT_MAX_FRAME_BYTES, CodecID, MsgType
from .ether import Ether
from .frames import Frame, FrameHeader, pack_frame, parse_frame


class Client:
    """XCP v0.2 client that connects to a server and sends frames."""

    def __init__(self, host: str, port: int, max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES, shared_mem: bool = False):
        """Initialize client.

        Args:
            host: Server hostname
            port: Server port
            max_frame_bytes: Maximum frame size
            shared_mem: Whether to enable shared memory
        """
        self.host = host
        self.port = port
        self.max_frame_bytes = max_frame_bytes
        self.shared_mem = shared_mem
        self._sock = None
        self._supported_codecs = []
        self._server_capabilities = {}
        self._msg_id = 1
        self._lock = threading.Lock()

        # Metrics tracking
        self._codec_usage = {0x01: 0, 0x08: 0}  # JSON, Protobuf
        self._total_bytes = 0

        self._connect()

    def _connect(self):
        """Establish connection and perform v0.2 handshake."""
        self._sock = socket.create_connection((self.host, self.port), timeout=5.0)

        # Send HELLO with capabilities
        hello_data = {
            "codecs": list_codecs(),
            "max_frame_bytes": self.max_frame_bytes,
            "shared_mem": self.shared_mem,
            "accepts": [],  # Accept all kinds for now
            "emits": [],  # Emit all kinds for now
        }

        # Encode HELLO using JSON codec
        json_codec = get_codec(CodecID.JSON)
        hello_payload = json_codec.encode(hello_data)

        hello_header = FrameHeader(msg_type=MsgType.HELLO, body_codec=CodecID.JSON, msg_id=self._msg_id)

        hello_frame = Frame(header=hello_header, payload=hello_payload)
        self._sock.sendall(pack_frame(hello_frame))

        # Expect CAPS response
        response = parse_frame(self._sock)
        if response.header.msg_type != MsgType.CAPS:
            raise RuntimeError("Handshake failed: expected CAPS")

        # Parse server capabilities
        json_codec = get_codec(CodecID.JSON)
        self._server_capabilities = json_codec.decode(response.payload)

        # Store intersection of supported codecs
        client_codecs = set(list_codecs())
        server_codecs = set(self._server_capabilities.get("codecs", []))
        self._supported_codecs = list(client_codecs & server_codecs)

        if not self._supported_codecs:
            raise RuntimeError("No common codecs supported")

    def send_ether(self, ether: Ether, codec_id: int = None) -> Frame:
        """Send an Ether envelope using the specified codec.

        Args:
            ether: The Ether envelope to send
            codec_id: Codec to use (if None, auto-selects based on payload size)

        Returns:
            Response frame
        """
        if codec_id is None:
            # Smart codec selection based on payload size
            payload_size = len(str(ether.model_dump()))
            if payload_size < 2048:  # 2KB threshold
                codec_id = 0x01  # JSON for small payloads
            else:
                codec_id = 0x08  # Protobuf for larger payloads
        else:
            # Use provided codec_id, calculate payload_size for metrics
            payload_size = len(str(ether.model_dump()))

        # Track codec usage metrics
        with self._lock:
            self._codec_usage[codec_id] += 1
            self._total_bytes += payload_size

        # Create frame header
        header = FrameHeader(channel_id=1, msg_type=MsgType.DATA, body_codec=codec_id, msg_id=1, in_reply_to=0)

        # Encode Ether using specified codec
        codec = get_codec(codec_id)
        payload = codec.encode(ether)

        # Create frame
        frame = Frame(header=header, payload=payload)

        # Send and get response
        response = self.request(frame)
        return response

    def request(self, frame: Frame) -> Frame:
        """Send a frame and wait for response.

        Args:
            frame: Frame to send

        Returns:
            Response frame

        Raises:
            ConnectionError: If connection is lost
        """
        with self._lock:
            frame.header.msg_id = self._msg_id
            self._msg_id += 1

        self._sock.sendall(pack_frame(frame))
        response = parse_frame(self._sock)
        return response

    def ping(self) -> Frame:
        """Send a PING frame.

        Returns:
            PONG response frame
        """
        ping_data = {"nonce": int(time.time() * 1000000)}
        json_codec = get_codec(CodecID.JSON)
        ping_payload = json_codec.encode(ping_data)

        with self._lock:
            frame = Frame(
                header=FrameHeader(msg_type=MsgType.PING, body_codec=CodecID.JSON, msg_id=self._msg_id),
                payload=ping_payload,
            )
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

    @property
    def supported_codecs(self) -> list[int]:
        """Get list of codecs supported by both client and server."""
        return self._supported_codecs.copy()

    @property
    def server_capabilities(self) -> dict[str, Any]:
        """Get server capabilities."""
        return self._server_capabilities.copy()

    @property
    def codec_metrics(self) -> dict[str, Any]:
        """Get codec usage metrics.

        Returns:
            Dictionary with codec usage statistics
        """
        with self._lock:
            total_requests = sum(self._codec_usage.values())
            if total_requests == 0:
                return {"json_percentage": 0, "protobuf_percentage": 0, "total_bytes": 0}

            json_percentage = (self._codec_usage[0x01] / total_requests) * 100
            protobuf_percentage = (self._codec_usage[0x08] / total_requests) * 100

            return {
                "json_percentage": json_percentage,
                "protobuf_percentage": protobuf_percentage,
                "total_bytes": self._total_bytes,
                "total_requests": total_requests,
            }

    def check_json_overuse(self, threshold: float = 1.0) -> bool:
        """Check if JSON is being used too much (>1% of byte volume).

        Args:
            threshold: Percentage threshold for JSON usage

        Returns:
            True if JSON usage exceeds threshold
        """
        metrics = self.codec_metrics
        return metrics["json_percentage"] > threshold

    def send_raw_payload(self, payload: bytes, codec_id: int = 0x01) -> bytes:
        """Send raw payload directly for benchmarking (bypasses Ether overhead).

        This is a performance optimization for benchmarks that don't need
        the full Ether envelope functionality.

        Args:
            payload: Raw bytes to send
            codec_id: Codec to use for encoding

        Returns:
            Raw response payload
        """
        # Create minimal frame header
        header = FrameHeader(channel_id=1, msg_type=MsgType.DATA, body_codec=codec_id, msg_id=1, in_reply_to=0)

        # Create frame with raw payload
        frame = Frame(header=header, payload=payload)

        # Send and get response
        response = self.request(frame)
        return response.payload
