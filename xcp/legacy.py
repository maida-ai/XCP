# mypy: ignore-errors
"""Legacy API compatibility module."""
import socket
import threading
import time
from contextlib import contextmanager

from .client import Client
from .constants import CodecID, MsgType
from .frames import Frame, FrameHeader
from .server import Server


class XCPConnection:
    """Legacy client connection API for backward compatibility."""

    def __init__(self, sock: socket.socket):
        """Initialize legacy connection.

        Args:
            sock: Pre-established socket
        """
        self._client = Client()
        self._client._sock = sock
        self._client._msg_id = 1

    def send(self, payload: bytes | str, codec: CodecID = CodecID.JSON) -> bytes:
        """Send payload and wait for echo response.

        Args:
            payload: Data to send
            codec: Codec to use

        Returns:
            Echoed payload
        """
        if isinstance(payload, str):
            payload_bytes = payload.encode()
        else:
            payload_bytes = payload

        header = FrameHeader(msgType=MsgType.DATA, bodyCodec=int(codec))
        frame = Frame(header=header, payload=payload_bytes)
        response = self._client.request(frame)
        return response.payload

    def close(self):
        """Close the connection."""
        self._client.close()


def open(host: str = "127.0.0.1", port: int = 9944, timeout: float = 5.0) -> XCPConnection:
    """Legacy function to open a client connection.

    Args:
        host: Server hostname
        port: Server port
        timeout: Connection timeout

    Returns:
        Legacy connection object
    """
    s = socket.create_connection((host, port), timeout=timeout)
    return XCPConnection(s)


class EchoServer(threading.Thread):
    """Legacy echo server for backward compatibility."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9944):
        """Initialize legacy echo server.

        Args:
            host: Host to bind to
            port: Port to bind to
        """
        super().__init__(name="xcp-echo-server")
        self.host = host
        self.port = port
        self._server = Server(host, port)
        self._running = threading.Event()

    def run(self):
        """Start the server."""
        self._running.set()
        self._server.serve_forever()

    def stop(self):
        """Stop the server."""
        self._running.clear()
        self._server.stop()


@contextmanager
def run_echo_server(host: str = "127.0.0.1", port: int = 9944):
    """Context manager to spin up an echo server in a background thread.

    Args:
        host: Host to bind to
        port: Port to bind to

    Yields:
        None
    """
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
