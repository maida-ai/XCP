"""XCP protocol constants and enums."""

from enum import IntEnum

# ----------------------------------------------------------------------------
# Protocol constants
# ----------------------------------------------------------------------------

MAGIC = 0xA9A17A10
MAJOR = 0x0
MINOR = 0x1
VERSION_BYTE = (MAJOR << 4) | MINOR

# ----------------------------------------------------------------------------
# Enums
# ----------------------------------------------------------------------------

class Flag(IntEnum):
    """Frame flags for compression, crypto, more data, and large payload."""
    COMP = 0b1000_0000
    CRYPT = 0b0100_0000
    MORE = 0b0010_0000
    LARGE = 0b0001_0000

class CodecID(IntEnum):
    """Supported codec identifiers."""
    JSON = 0x01
    TENSOR_F32 = 0x02
    TENSOR_F16 = 0x03
    TENSOR_QNT8 = 0x04
    BINARY = 0x10  # raw bytes, no interpretation

class MsgType(IntEnum):
    """Message type identifiers."""
    HELLO = 0x01
    CAPS_ACK = 0x02
    PING = 0x03
    PONG = 0x04
    NEGOTIATE = 0x05
    UNSUPPORTED = 0x06
    ACK = 0x07
    NACK = 0x08
    DATA = 0x20
