"""XCP v0.2 codec implementations."""

from typing import Any

from ..ether import Ether
from .base import Codec

# Import all codec implementations
from .json_codec import JSONCodec
from .protobuf_codec import ProtobufCodec

__all__ = [
    "Codec",
    "Ether",
    "JSONCodec",
    "ProtobufCodec",
    "register_codec",
    "get_codec",
    "list_codecs",
]


# Codec registry
_CODECS: dict[int, type[Codec]] = {}


def register_codec(codec_id: int, codec_class: type[Codec]) -> None:
    """Register a codec implementation."""
    _CODECS[codec_id] = codec_class


def get_codec(codec_id: int) -> Codec:
    """Get a codec instance by ID."""
    if codec_id not in _CODECS:
        raise ValueError(f"Unsupported codec ID: {codec_id}")
    return _CODECS[codec_id]()


def list_codecs() -> list[int]:
    """List all registered codec IDs."""
    return list(_CODECS.keys())


# Register default codecs
register_codec(0x0001, JSONCodec)  # JSON
register_codec(0x0008, ProtobufCodec)  # PROTOBUF
