"""XCP client implementation."""

import socket
import threading
import time
import uuid
from typing import Optional

from .constants import MsgType, CodecID
from .frames import Frame, FrameHeader, pack_frame, parse_frame

class Client:
    """XCP client for sending frames to a server."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9944, enable_cache_busting: bool = False):
        """Initialize client and establish connection.

        Args:
            host: Server hostname
            port: Server port
            enable_cache_busting: Whether to enable cache-busting measures

        Raises:
            RuntimeError: If handshake fails
        """
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._msg_id = 1
        self._lock = threading.Lock()
        self._enable_cache_busting = enable_cache_busting
        self._connection_id = str(uuid.uuid4()) if enable_cache_busting else "default"
        self._connect()

    def _connect(self):
        """Establish connection and perform handshake."""
        self._sock = socket.create_connection((self.host, self.port), timeout=5.0)

        # Send HELLO with cache-busting if enabled
        hello_header = FrameHeader(
            msgType=MsgType.HELLO,
            bodyCodec=CodecID.JSON,
            msgId=self._msg_id
        )

        # Add cache-busting payload if enabled
        hello_payload = b""
        if self._enable_cache_busting:
            hello_payload = f"cache-bust-{self._connection_id}-{time.time()}".encode()

        hello_frame = Frame(header=hello_header, payload=hello_payload)
        self._sock.sendall(pack_frame(hello_frame))

        # Expect CAPS_ACK
        response = parse_frame(self._sock)
        if response.header.msgType != MsgType.CAPS_ACK:
            raise RuntimeError("Handshake failed: expected CAPS_ACK")

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
            # Ensure unique message IDs to prevent any potential caching
            if self._enable_cache_busting:
                # Use microsecond precision timestamp + counter for guaranteed uniqueness
                timestamp = int(time.time() * 1000000)  # microseconds
                frame.header.msgId = timestamp + self._msg_id
                self._msg_id += 1
                # Small delay to ensure timestamp changes between requests
                time.sleep(0.000001)  # 1 microsecond delay
            else:
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
