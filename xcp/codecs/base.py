"""Base codec interface for XCP v0.2."""

from abc import ABC, abstractmethod
from typing import Any


class Codec(ABC):
    """Base interface for XCP codecs."""

    @abstractmethod
    def encode(self, data: Any) -> bytes:
        """Encode data to bytes."""
        pass

    @abstractmethod
    def decode(self, data: bytes) -> Any:
        """Decode bytes to data."""
        pass
