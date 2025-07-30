# mypy: ignore-errors
"""Protobuf codec implementation for XCP v0.2."""
from typing import Any

from ..ether import Attachment, Ether
from . import Codec


class ProtobufCodec(Codec):
    """Protobuf codec for control messages and Ether envelopes."""

    def encode(self, data: Any) -> bytes:
        """Encode data to Protobuf bytes.

        Args:
            data: Data to encode (Ether object or control message)

        Returns:
            Serialized Protobuf bytes
        """
        if isinstance(data, Ether):
            return self._encode_ether(data)
        else:
            # For control messages, assume it's already a protobuf message
            return data.SerializeToString()

    def decode(self, data: bytes) -> Any:
        """Decode Protobuf bytes to data.

        Args:
            data: Serialized Protobuf bytes

        Returns:
            Decoded data (Ether object or protobuf message)
        """
        # Try to decode as Ether first
        try:
            return self._decode_ether(data)
        except Exception:
            # If that fails, return raw bytes for control messages
            return data

    def _encode_ether(self, ether: Ether) -> bytes:
        """Encode Ether to Protobuf bytes."""
        try:
            # Import here to avoid circular imports
            from ..generated import ether_pb2 as ep

            msg = ep.EtherProto(
                kind=ether.kind,
                schema_version=ether.schema_version,
            )

            # Convert payload, metadata, and extra_fields to bytes
            for key, value in ether.payload.items():
                if isinstance(value, str | int | float | bool):
                    msg.payload[key] = str(value).encode()
                else:
                    msg.payload[key] = str(value).encode()

            for key, value in ether.metadata.items():
                if isinstance(value, str | int | float | bool):
                    msg.metadata[key] = str(value).encode()
                else:
                    msg.metadata[key] = str(value).encode()

            for key, value in ether.extra_fields.items():
                if isinstance(value, str | int | float | bool):
                    msg.extra_fields[key] = str(value).encode()
                else:
                    msg.extra_fields[key] = str(value).encode()

            # Handle attachments
            for attachment in ether.attachments:
                pb_attachment = ep.Attachment(
                    id=attachment.id,
                    uri=attachment.uri or "",
                    media_type=attachment.media_type or "",
                    codec=attachment.codec or "",
                    shape=attachment.shape or [],
                    dtype=attachment.dtype or "",
                    size_bytes=attachment.size_bytes or 0,
                    inline_bytes=attachment.inline_bytes or b"",
                )
                msg.attachments.append(pb_attachment)

            return msg.SerializeToString()

        except ImportError:
            # Fallback to JSON if protobuf not available
            import json

            return json.dumps(ether.model_dump(), separators=(",", ":")).encode()

    def _decode_ether(self, data: bytes) -> Ether:
        """Decode Protobuf bytes to Ether."""
        try:
            # Import here to avoid circular imports
            from ..generated import ether_pb2 as ep

            msg = ep.EtherProto()
            msg.ParseFromString(data)

            # Convert bytes back to appropriate types
            payload = {}
            for key, value in msg.payload.items():
                try:
                    # Try to convert back to original type
                    payload[key] = value.decode()
                except Exception:
                    payload[key] = value

            metadata = {}
            for key, value in msg.metadata.items():
                try:
                    metadata[key] = value.decode()
                except Exception:
                    metadata[key] = value

            extra_fields = {}
            for key, value in msg.extra_fields.items():
                try:
                    extra_fields[key] = value.decode()
                except Exception:
                    extra_fields[key] = value

            # Convert attachments
            attachments = []
            for pb_attachment in msg.attachments:
                attachment = Attachment(
                    id=pb_attachment.id,
                    uri=pb_attachment.uri if pb_attachment.uri else None,
                    media_type=pb_attachment.media_type if pb_attachment.media_type else None,
                    codec=pb_attachment.codec if pb_attachment.codec else None,
                    shape=list(pb_attachment.shape) if pb_attachment.shape else None,
                    dtype=pb_attachment.dtype if pb_attachment.dtype else None,
                    size_bytes=pb_attachment.size_bytes if pb_attachment.size_bytes else None,
                    inline_bytes=pb_attachment.inline_bytes if pb_attachment.inline_bytes else None,
                )
                attachments.append(attachment)

            return Ether(
                kind=msg.kind,
                schema_version=msg.schema_version,
                payload=payload,
                metadata=metadata,
                extra_fields=extra_fields,
                attachments=attachments,
            )

        except ImportError:
            # Fallback to JSON if protobuf not available
            import json

            decoded = json.loads(data.decode())
            return Ether(**decoded)
