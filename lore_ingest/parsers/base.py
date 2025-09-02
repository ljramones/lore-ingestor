from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Set


# ---- Exceptions ------------------------------------------------------------
class ParseError(Exception):
    """Generic parse error for ingestion."""


class UnsupportedFileType(ParseError):
    """Raised when attempting to parse an unsupported file extension."""


class DependencyMissing(ParseError):
    """Raised when an optional dependency for a parser is missing."""


# ---- Result type -----------------------------------------------------------
@dataclass
class ParseResult:
    """Normalized output of any file parser."""
    raw: bytes
    text: str
    meta: Dict[str, Any] | None = None


# ---- Parser base classes ---------------------------------------------------
class BaseParser:
    """Base class for file parsers."""
    exts: Set[str] = set()
    binary: bool = False

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() in self.exts

    def parse_path(self, path: Path) -> ParseResult:  # pragma: no cover
        raise NotImplementedError("parse_path must be implemented by subclasses")


class TextParser(BaseParser):
    """Marker for parsers whose primary output is text (default)."""
    binary: bool = False


class BinaryParser(BaseParser):
    """Marker for parsers that handle binary formats but still output text."""
    binary: bool = True


__all__ = [
    "ParseError",
    "UnsupportedFileType",
    "DependencyMissing",
    "ParseResult",
    "BaseParser",
    "TextParser",
    "BinaryParser",
]
