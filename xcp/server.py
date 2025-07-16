"""XCP server implementation."""

import logging
import socket
import threading
from typing import Callable, Optional

from .constants import MsgType, CodecID
from .frames import Frame, FrameHeader, pack_frame, parse_frame

class _ClientHandler(threading.Thread):
    """Handle a single client connection."""

    def __init__(self, sock: socket.socket, addr, on_frame: Callable[[Frame], Frame]):
        """Initialize client handler.

        Args:
            sock: Client socket
            addr: Client address
            on_frame: Frame handler callback
        """
        super().__init__(daemon=True)
        self.sock = sock
        self.addr = addr
        self.on_frame = on_frame
        self.running = True

    def run(self):
        """Handle client connection."""
        try:
            self._serve()
        except Exception as exc:
            logging.debug("Client %s closed: %s", self.addr, exc)
        finally:
            self.sock.close()

    def _serve(self):
        """Serve client requests."""
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
        """Initialize server.

        Args:
            host: Host to bind to
            port: Port to bind to
            on_frame: Optional frame handler callback
        """
        self.host = host
        self.port = port
        self.on_frame = on_frame or self._default_handler
        self._sock: Optional[socket.socket] = None
        self._running = threading.Event()

    def _default_handler(self, frame: Frame) -> Frame:
        """Default handler that echoes DATA frames.

        Args:
            frame: Received frame

        Returns:
            Response frame or None
        """
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
