r"""XCP (eXtensible Coordination Protocol) - A binary-first communication protocol for AI agents.

This package provides a minimal proof-of-concept implementation of the XCP v0.1 specification
sufficient for local echo benchmarks and performance comparisons.

The implementation follows the XCP v0.1 specification and provides:
- Binary frame format with proper headers
- Client/Server classes for the benchmark
- Frame and FrameHeader classes
- Support for JSON and binary codecs
- Basic handshake protocol
"""

# Import public API from modules
from .constants import (
    MAGIC,
    VERSION_BYTE,
    Flag,
    CodecID,
    MsgType,
)

from .frames import (
    Frame,
    FrameHeader,
    pack_frame,
    parse_frame,
)

from .client import Client
from .server import Server

# Legacy API for backward compatibility
from .legacy import (
    XCPConnection,
    open,
    EchoServer,
    run_echo_server,
)

# Public API exports
__all__ = [
    # Core classes
    "Frame",
    "FrameHeader",
    "Client",
    "Server",

    # Constants and enums
    "MAGIC",
    "VERSION_BYTE",
    "Flag",
    "CodecID",
    "MsgType",

    # Frame utilities
    "pack_frame",
    "parse_frame",

    # Legacy API
    "open",
    "run_echo_server",
    "XCPConnection",
    "EchoServer",
]
