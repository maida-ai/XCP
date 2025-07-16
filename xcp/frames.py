"""XCP frame structures and serialization."""

import json
import socket
import struct
from dataclasses import dataclass
from typing import Optional

from .constants import MAGIC, VERSION_BYTE, Flag, MsgType, CodecID

# ----------------------------------------------------------------------------
# Frame structures
# ----------------------------------------------------------------------------

@dataclass
class FrameHeader:
    """XCP frame header containing metadata about the frame."""
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
        """Convert header to dictionary for JSON serialization."""
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
    def from_dict(cls, data: dict) -> "FrameHeader":
        """Create header from dictionary."""
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
    """XCP frame containing header and payload."""
    header: FrameHeader
    payload: bytes

    def __post_init__(self):
        if isinstance(self.payload, str):
            self.payload = self.payload.encode()

# ----------------------------------------------------------------------------
# Frame serialization/deserialization
# ----------------------------------------------------------------------------

def pack_frame(frame: Frame, flags: int = 0) -> bytes:
    """Pack a Frame into the XCP binary format.

    Args:
        frame: The frame to serialize
        flags: Optional flags to set

    Returns:
        Binary representation of the frame
    """
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
    """Receive exactly n bytes from socket.

    Args:
        sock: Socket to receive from
        n: Number of bytes to receive

    Returns:
        Received bytes

    Raises:
        ConnectionError: If connection is closed unexpectedly
    """
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Unexpected EOF from peer")
        buf.extend(chunk)
    return bytes(buf)

def parse_frame(sock: socket.socket) -> Frame:
    """Parse an XCP frame from socket.

    Args:
        sock: Socket to read from

    Returns:
        Parsed frame

    Raises:
        ValueError: If frame format is invalid
        ConnectionError: If connection is closed unexpectedly
    """
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
