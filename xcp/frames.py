"""XCP v0.2 frame structures and serialization."""

import json
import socket
import struct
from dataclasses import dataclass, field

from google_crc32c import Checksum

from .constants import MAGIC, VERSION_BYTE, CodecID, Flag, MsgType

# ----------------------------------------------------------------------------
# Frame structures
# ----------------------------------------------------------------------------


@dataclass
class SchemaKey:
    """Schema key for identifying Ether schemas."""

    ns_hash: int = 0
    kind_id: int = 0
    major: int = 0
    minor: int = 0
    hash128: bytes = b"\x00" * 16


@dataclass
class Tag:
    """Free-form key/value tag."""

    key: str
    val: str


@dataclass
class FrameHeader:
    """XCP v0.2 frame header using JSON serialization."""

    channel_id: int = 0
    msg_type: int = MsgType.DATA
    body_codec: int = CodecID.JSON
    schema_key: SchemaKey = field(default_factory=SchemaKey)
    msg_id: int = 0
    in_reply_to: int = 0
    tags: list[Tag] = field(default_factory=list)

    def to_bytes(self) -> bytes:
        """Convert header to JSON bytes."""
        data = {
            "channelId": self.channel_id,
            "msgType": self.msg_type,
            "bodyCodec": self.body_codec,
            "schemaKey": {
                "nsHash": self.schema_key.ns_hash,
                "kindId": self.schema_key.kind_id,
                "major": self.schema_key.major,
                "minor": self.schema_key.minor,
                "hash128": self.schema_key.hash128.hex(),
            },
            "msgId": self.msg_id,
            "inReplyTo": self.in_reply_to,
            "tags": [{"key": tag.key, "val": tag.val} for tag in self.tags],
        }
        return json.dumps(data, separators=(",", ":")).encode()

    @classmethod
    def from_bytes(cls, data: bytes) -> "FrameHeader":
        """Create header from JSON bytes."""
        decoded = json.loads(data.decode())

        schema_data = decoded.get("schemaKey", {})
        schema = SchemaKey(
            ns_hash=schema_data.get("nsHash", 0),
            kind_id=schema_data.get("kindId", 0),
            major=schema_data.get("major", 0),
            minor=schema_data.get("minor", 0),
            hash128=bytes.fromhex(schema_data.get("hash128", "00" * 16)),
        )

        tags = []
        for tag_data in decoded.get("tags", []):
            tags.append(Tag(key=tag_data["key"], val=tag_data["val"]))

        return cls(
            channel_id=decoded.get("channelId", 0),
            msg_type=decoded.get("msgType", MsgType.DATA),
            body_codec=decoded.get("bodyCodec", CodecID.JSON),
            schema_key=schema,
            msg_id=decoded.get("msgId", 0),
            in_reply_to=decoded.get("inReplyTo", 0),
            tags=tags,
        )


@dataclass
class Frame:
    """XCP v0.2 frame containing header and payload."""

    header: FrameHeader
    payload: bytes

    def __post_init__(self) -> None:
        if isinstance(self.payload, str):  # type: ignore[unreachable]
            self.payload = self.payload.encode()  # type: ignore[unreachable]


# ----------------------------------------------------------------------------
# Frame serialization/deserialization
# ----------------------------------------------------------------------------


def pack_frame(frame: Frame, flags: int = 0) -> bytes:
    """Pack a Frame into the XCP v0.2 binary format.

    Args:
        frame: The frame to serialize
        flags: Optional flags to set

    Returns:
        Binary representation of the frame
    """
    # Serialize header
    header_bytes = frame.header.to_bytes()
    hlen = len(header_bytes)

    # Determine payload length and LARGE flag
    payload_len = len(frame.payload)
    large = payload_len >= 2**32
    if large:
        flags |= Flag.LARGE
        plen_field = struct.pack("<Q", payload_len)  # 8-byte, little-endian
    else:
        plen_field = struct.pack("<I", payload_len)  # 4-byte, little-endian

    # Build frame prefix
    prefix = struct.pack("<I B B H", MAGIC, VERSION_BYTE, flags, hlen)
    frame_data = prefix + header_bytes + plen_field + frame.payload

    # Add CRC32C footer
    crc = Checksum()
    crc.update(frame.payload)
    crc_bytes = struct.pack("<I", int.from_bytes(crc.digest(), "little"))

    return frame_data + crc_bytes


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
    """Parse an XCP v0.2 frame from socket.

    Args:
        sock: Socket to read from

    Returns:
        Parsed frame

    Raises:
        ValueError: If frame format is invalid
        ConnectionError: If connection is closed unexpectedly
    """
    # Read first fixed 8 bytes (little-endian)
    pre = recv_exact(sock, 8)
    magic, ver, flags, hlen = struct.unpack("<I B B H", pre)
    if magic != MAGIC:
        raise ValueError("Bad MAGIC header")

    # Read header
    header_raw = recv_exact(sock, hlen)
    header = FrameHeader.from_bytes(header_raw)

    # Determine PLEN length
    if flags & Flag.LARGE:
        plen_bytes = recv_exact(sock, 8)
        payload_len = struct.unpack("<Q", plen_bytes)[0]
    else:
        plen_bytes = recv_exact(sock, 4)
        payload_len = struct.unpack("<I", plen_bytes)[0]

    # Read payload
    payload = recv_exact(sock, payload_len)

    # Read and verify CRC32C
    crc_bytes = recv_exact(sock, 4)
    expected_crc = struct.unpack("<I", crc_bytes)[0]

    crc = Checksum()
    crc.update(payload)
    actual_crc = int.from_bytes(crc.digest(), "little")

    if actual_crc != expected_crc:
        raise ValueError("CRC32C mismatch")

    return Frame(header=header, payload=payload)
