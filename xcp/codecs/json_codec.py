"""JSON codec implementation for XCP v0.2."""

import json
from typing import Any

from ..ether import Ether
from . import Codec


class JSONCodec(Codec):
    """JSON codec for human-readable debug and small messages."""

    def encode(self, data: Any) -> bytes:
        """Encode data to JSON bytes.

        Args:
            data: Data to encode (Ether object or dict)

        Returns:
            UTF-8 encoded JSON bytes
        """
        if isinstance(data, Ether):
            # Convert Ether to dict with proper datetime handling
            json_data = data.model_dump()
        else:
            json_data = data

        return json.dumps(json_data, separators=(",", ":")).encode("utf-8")

    def decode(self, data: bytes) -> Any:
        """Decode JSON bytes to data.

        Args:
            data: UTF-8 encoded JSON bytes

        Returns:
            Decoded data (dict or Ether object)
        """
        json_str = data.decode("utf-8")
        decoded = json.loads(json_str)

        # Try to convert to Ether if it has the right structure
        if isinstance(decoded, dict) and "kind" in decoded and "schema_version" in decoded:
            try:
                return Ether(**decoded)
            except Exception:
                # If conversion fails, return as dict
                pass

        return decoded
