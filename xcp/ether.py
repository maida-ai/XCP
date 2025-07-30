"""Ether envelope implementation for XCP v0.2."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Attachment(BaseModel):
    """Binary attachment for Ether envelope."""

    id: str
    uri: str | None = None
    media_type: str | None = None
    codec: str | None = None
    shape: list[int] | None = None
    dtype: str | None = None
    size_bytes: int | None = None
    inline_bytes: bytes | None = None


class Ether(BaseModel):
    """Self-describing data envelope for XCP v0.2."""

    kind: str = Field(..., description="Logical type identifier")
    schema_version: int = Field(..., ge=1, description="Additive integer version")
    payload: dict[str, Any] = Field(default_factory=dict, description="Kind-defined data")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Free-form metadata")
    extra_fields: dict[str, Any] = Field(default_factory=dict, description="Unclassified data")
    attachments: list[Attachment] = Field(default_factory=list, description="Binary attachments")

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        """Convert to dictionary with proper datetime handling."""
        data: dict = super().model_dump(**kwargs)

        # Convert datetime objects to ISO format strings
        for key, value in data.items():
            if isinstance(value, dict):
                for k, v in value.items():
                    if isinstance(v, datetime):
                        value[k] = v.isoformat()
            elif isinstance(value, datetime):
                data[key] = value.isoformat()

        return data

    @classmethod
    def create_text(cls, text: str, **kwargs: Any) -> "Ether":
        """Create a text Ether envelope."""
        return cls(kind="text", schema_version=1, payload={"text": text}, **kwargs)

    @classmethod
    def create_embedding(cls, values: list[float], dim: int, **kwargs: Any) -> "Ether":
        """Create an embedding Ether envelope."""
        return cls(kind="embedding", schema_version=1, payload={"values": values, "dim": dim}, **kwargs)

    @classmethod
    def create_tokens(cls, token_ids: list[int], mask: list[bool] | None = None, **kwargs: Any) -> "Ether":
        """Create a tokens Ether envelope."""
        payload = {"token_ids": token_ids}
        if mask is not None:
            payload["mask"] = mask  # type: ignore[assignment]

        return cls(kind="tokens", schema_version=1, payload=payload, **kwargs)

    @classmethod
    def create_image(cls, height: int, width: int, channels: int, data: bytes, **kwargs: Any) -> "Ether":
        """Create an image Ether envelope."""
        return cls(
            kind="image",
            schema_version=1,
            payload={"height": height, "width": width, "channels": channels, "data": data},
            **kwargs,
        )
