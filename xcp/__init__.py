# Copyright 2025 Maida.AI
# SPDX-License-Identifier: Apache-2.0
r"""XCP (eXtensible Coordination Protocol) - A binary-first communication protocol for AI agents.

This package provides a minimal proof-of-concept implementation of the XCP v0.2 specification
sufficient for local echo benchmarks and performance comparisons.

The implementation follows the XCP v0.2 specification and provides:
- Binary frame format with Cap'n Proto headers and CRC32C validation
- Client/Server classes with capability negotiation
- Frame and FrameHeader classes with proper v0.2 structure
- Support for JSON and Protobuf codecs
- Ether envelope for self-describing data
- Basic handshake protocol with HELLO/CAPS exchange
"""

# Import public API from modules
from .client import Client
from .codecs import (
    Codec,
    get_codec,
    list_codecs,
    register_codec,
)
from .constants import (
    DEFAULT_MAX_FRAME_BYTES,
    KIND_EMBEDDING,
    KIND_IMAGE,
    KIND_TEXT,
    KIND_TOKENS,
    LAN_MAX_FRAME_BYTES,
    MAGIC,
    VERSION_BYTE,
    WAN_MAX_FRAME_BYTES,
    CodecID,
    ErrorCode,
    Flag,
    MsgType,
)
from .ether import (
    Attachment,
    Ether,
)
from .frames import (
    Frame,
    FrameHeader,
    SchemaKey,
    Tag,
    pack_frame,
    parse_frame,
)

# Legacy API for backward compatibility
from .legacy import (
    EchoServer,
    XCPConnection,
    open,
    run_echo_server,
)
from .server import Server

# Public API exports
__all__ = [
    # Core classes
    "Frame",
    "FrameHeader",
    "SchemaKey",
    "Tag",
    "Client",
    "Server",
    "Ether",
    "Attachment",
    "Codec",
    # Constants and enums
    "MAGIC",
    "VERSION_BYTE",
    "Flag",
    "CodecID",
    "MsgType",
    "ErrorCode",
    "KIND_TEXT",
    "KIND_TOKENS",
    "KIND_EMBEDDING",
    "KIND_IMAGE",
    "DEFAULT_MAX_FRAME_BYTES",
    "WAN_MAX_FRAME_BYTES",
    "LAN_MAX_FRAME_BYTES",
    # Frame utilities
    "pack_frame",
    "parse_frame",
    # Codec utilities
    "get_codec",
    "list_codecs",
    "register_codec",
    # Legacy API
    "open",
    "run_echo_server",
    "XCPConnection",
    "EchoServer",
]
