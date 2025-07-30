# mypy: ignore-errors
"""XCP v0.2 server implementation."""
import logging
import socket
import threading
from collections.abc import Callable

from .codecs import get_codec, list_codecs
from .constants import DEFAULT_MAX_FRAME_BYTES, CodecID, ErrorCode, MsgType
from .ether import Ether
from .frames import Frame, FrameHeader, pack_frame, parse_frame


class _ClientHandler(threading.Thread):
    """Handle a single client connection."""

    def __init__(
        self, sock: socket.socket, addr, on_frame: Callable[[Frame], Frame], on_ether: Callable[[Ether], Ether] = None
    ):
        """Initialize client handler.

        Args:
            sock: Client socket
            addr: Client address
            on_frame: Frame handler callback
            on_ether: Ether handler callback
        """
        super().__init__(daemon=True)
        self.sock = sock
        self.addr = addr
        self.on_frame = on_frame
        self.on_ether = on_ether
        self.running = True
        self._supported_codecs: list[int] = []

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
        if frame.header.msg_type != MsgType.HELLO:
            return

        # Parse client capabilities
        json_codec = get_codec(CodecID.JSON)
        client_capabilities = json_codec.decode(frame.payload)

        # Determine intersection of codecs
        client_codecs = set(client_capabilities.get("codecs", []))
        server_codecs = set(list_codecs())
        self._supported_codecs = list(client_codecs & server_codecs)

        # Send CAPS response
        caps_data = {
            "codecs": self._supported_codecs,
            "max_frame_bytes": DEFAULT_MAX_FRAME_BYTES,
            "shared_mem": False,  # TODO: implement shared memory
            "accepts": [],  # Accept all kinds for now
            "emits": [],  # Emit all kinds for now
        }

        caps_payload = json_codec.encode(caps_data)
        caps_header = FrameHeader(msg_type=MsgType.CAPS, body_codec=CodecID.JSON, in_reply_to=frame.header.msg_id)
        caps_frame = Frame(header=caps_header, payload=caps_payload)
        self.sock.sendall(pack_frame(caps_frame))

        # Handle frames
        while self.running:
            try:
                frame = parse_frame(self.sock)
                response = self._handle_frame(frame)
                if response:
                    self.sock.sendall(pack_frame(response))
            except (ConnectionError, ValueError) as e:
                logging.debug("Client %s error: %s", self.addr, e)
                break

    def _handle_frame(self, frame: Frame) -> Frame | None:
        """Handle a received frame.

        Args:
            frame: Received frame

        Returns:
            Response frame or None
        """
        if frame.header.msg_type == MsgType.DATA:
            return self._handle_data_frame(frame)
        elif frame.header.msg_type == MsgType.PING:
            return self._handle_ping_frame(frame)
        else:
            # Let custom handler deal with other frame types
            return self.on_frame(frame) if self.on_frame else None

    def _handle_data_frame(self, frame: Frame) -> Frame | None:
        """Handle a DATA frame containing Ether or raw payload."""
        try:
            # Check if this is a raw payload (for benchmarking)
            if frame.header.body_codec in [0x01, 0x08] and len(frame.payload) > 0:
                # Try to decode as Ether first
                try:
                    codec = get_codec(frame.header.body_codec)
                    ether_data = codec.decode(frame.payload)

                    if isinstance(ether_data, Ether):
                        # Process Ether through custom handler
                        if self.on_ether:
                            response_ether = self.on_ether(ether_data)
                            if response_ether:
                                # Encode response using same codec
                                response_payload = codec.encode(response_ether)
                                response_header = FrameHeader(
                                    channel_id=frame.header.channel_id,
                                    msg_type=MsgType.DATA,
                                    body_codec=frame.header.body_codec,
                                    in_reply_to=frame.header.msg_id,
                                )
                                return Frame(header=response_header, payload=response_payload)
                        else:
                            # Default echo behavior for Ether
                            response_header = FrameHeader(
                                channel_id=frame.header.channel_id,
                                msg_type=MsgType.DATA,
                                body_codec=frame.header.body_codec,
                                in_reply_to=frame.header.msg_id,
                            )
                            return Frame(header=response_header, payload=frame.payload)
                except Exception:
                    # If Ether decoding fails, treat as raw payload
                    pass

            # Handle as raw payload (for benchmarking)
            response_header = FrameHeader(
                channel_id=frame.header.channel_id,
                msg_type=MsgType.DATA,
                body_codec=frame.header.body_codec,
                in_reply_to=frame.header.msg_id,
            )
            return Frame(header=response_header, payload=frame.payload)

        except Exception as e:
            logging.error("Error handling DATA frame: %s", e)
            # Send NACK
            nack_data = {
                "msg_id": frame.header.msg_id,
                "error_code": ErrorCode.ERR_CODEC_UNSUPPORTED,
                "retry_after_ms": 0,
            }
            json_codec = get_codec(CodecID.JSON)
            nack_payload = json_codec.encode(nack_data)
            nack_header = FrameHeader(msg_type=MsgType.NACK, body_codec=CodecID.JSON, in_reply_to=frame.header.msg_id)
            return Frame(header=nack_header, payload=nack_payload)

    def _handle_ping_frame(self, frame: Frame) -> Frame:
        """Handle a PING frame."""
        # Echo PING as PONG
        pong_header = FrameHeader(
            msg_type=MsgType.PONG, body_codec=frame.header.body_codec, in_reply_to=frame.header.msg_id
        )
        return Frame(header=pong_header, payload=frame.payload)


class Server:
    """XCP v0.2 server that accepts connections and handles frames."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9944,
        on_frame: Callable[[Frame], Frame] = None,
        on_ether: Callable[[Ether], Ether] = None,
    ):
        """Initialize server.

        Args:
            host: Host to bind to
            port: Port to bind to
            on_frame: Optional frame handler callback
            on_ether: Optional Ether handler callback
        """
        self.host = host
        self.port = port
        self.on_frame = on_frame
        self.on_ether = on_ether or self._default_ether_handler
        self._sock: socket.socket | None = None
        self._running = threading.Event()

    def _default_ether_handler(self, ether: Ether) -> Ether:
        """Default handler that echoes Ether envelopes.

        Args:
            ether: Received Ether envelope

        Returns:
            Echoed Ether envelope
        """
        return ether

    def serve_forever(self):
        """Start the server and handle connections."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self.host, self.port))
            srv.listen()
            self._sock = srv
            self._running.set()

            logging.info("XCP v0.2 server listening on %s:%d", self.host, self.port)

            while self._running.is_set():
                try:
                    cli_sock, addr = srv.accept()
                    _ClientHandler(cli_sock, addr, self.on_frame, self.on_ether).start()
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
