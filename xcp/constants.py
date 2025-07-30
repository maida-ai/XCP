"""XCP protocol constants and enums for v0.2."""

from enum import IntEnum

# ----------------------------------------------------------------------------
# Protocol constants
# ----------------------------------------------------------------------------

MAGIC = 0xA9A17A10  # ASCII "XCP\n"
MAJOR = 0x0
MINOR = 0x2
VERSION_BYTE = (MAJOR << 4) | MINOR  # 0x02 for v0.2

# ----------------------------------------------------------------------------
# Frame flags
# ----------------------------------------------------------------------------


class Flag(IntEnum):
    """Frame flags for compression, crypto, more data, and large payload."""

    COMP = 0b1000_0000  # zstd compression
    CRYPT = 0b0100_0000  # ChaCha20-Poly1305 AEAD
    MORE = 0b0010_0000  # chunking (more frames follow)
    LARGE = 0b0001_0000  # 8-byte payload length


# ----------------------------------------------------------------------------
# Codec identifiers
# ----------------------------------------------------------------------------


class CodecID(IntEnum):
    """Supported codec identifiers for v0.2."""

    JSON = 0x0001  # Human-readable debug / small messages
    TENSOR_F32 = 0x0002  # Raw LE float32 (with header)
    TENSOR_F16 = 0x0003  # Raw LE float16
    TENSOR_QNT8 = 0x0004  # INT8 + per-row scale/zero-pt
    MIXED_LATENT = 0x0010  # Varint-delimited tensor segments
    PROTOBUF = 0x0008  # Protobuf control/data messages
    ARROW_IPC = 0x0020  # Columnar batches / tables
    DLPACK = 0x0021  # GPU tensor hand-off
    RESERVED = 0x00FF  # Reserved for future use


# ----------------------------------------------------------------------------
# Message types
# ----------------------------------------------------------------------------


class MsgType(IntEnum):
    """Message type identifiers for v0.2."""

    # Control messages (0x0000-0x00FF)
    HELLO = 0x0000  # Agent capabilities
    CAPS = 0x0001  # Capability response
    ACK = 0x0002  # Acknowledges msgId
    NACK = 0x0003  # Numeric error_code
    PING = 0x0004  # Keep-alive
    PONG = 0x0005  # Response
    CLARIFY_REQ = 0x0006  # Ask peer to supply missing fields
    CLARIFY_RES = 0x0007  # Answer with inReplyTo

    # Data messages (0x0100+)
    DATA = 0x0100  # Ether payload


# ----------------------------------------------------------------------------
# Error codes
# ----------------------------------------------------------------------------


class ErrorCode(IntEnum):
    """Numeric error codes for NACK messages."""

    OK = 0x0000
    ERR_SCHEMA_UNKNOWN = 0x0001
    ERR_CODEC_UNSUPPORTED = 0x0002
    ERR_MESSAGE_TOO_LARGE = 0x0003
    ERR_KIND_MISMATCH = 0x0004


# ----------------------------------------------------------------------------
# Schema constants
# ----------------------------------------------------------------------------

# Standard Ether kinds (baseline)
KIND_TEXT = 0x6EA7E21B  # "text" FNV-1a hash
KIND_TOKENS = 0x8B4E3A2C  # "tokens" FNV-1a hash
KIND_EMBEDDING = 0x9C5F4B3D  # "embedding" FNV-1a hash
KIND_IMAGE = 0xAD6F5C4E  # "image" FNV-1a hash

# Default frame size limits
DEFAULT_MAX_FRAME_BYTES = 1 << 20  # 1 MiB
WAN_MAX_FRAME_BYTES = 512 << 10  # 512 KiB
LAN_MAX_FRAME_BYTES = 4 << 20  # 4 MiB
