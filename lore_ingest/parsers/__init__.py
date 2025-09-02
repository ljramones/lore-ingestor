# lore_ingest/parsers/__init__.py
from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Dict, Type, Optional

from .base import (
    TextParser,
    ParseResult,
    UnsupportedFileType,
    DependencyMissing,
    ParseError,
)

# Registry (ext -> parser)
_REGISTRY: Dict[str, TextParser] = {}


def _register(parser: TextParser) -> None:
    for ext in getattr(parser, "exts", []):
        _REGISTRY[ext.lower()] = parser


def _ensure_builtins_loaded() -> None:
    """
    Import built-in parser modules once so they can self-register.
    """
    # Import modules for side-effect of setting PARSER
    for mod in (".txt_md", ".pdf", ".docx"):
        import_module(__name__ + mod)

    # Pick up PARSER objects exported from each module
    from .txt_md import PARSER as TXTMD  # type: ignore
    from .pdf import PARSER as PDF  # type: ignore
    from .docx import PARSER as DOCX  # type: ignore

    for p in (TXTMD, PDF, DOCX):
        _register(p)


_BUILTINS_READY = False


def available_parsers() -> Dict[str, str]:
    """
    Returns a map of extension -> parser-name for debugging/inspection.
    """
    global _BUILTINS_READY
    if not _BUILTINS_READY:
        _ensure_builtins_loaded()
        _BUILTINS_READY = True
    return {ext: getattr(p, "__class__").__name__ for ext, p in _REGISTRY.items()}


def get_parser_for_path(path: Path) -> TextParser:
    """
    Returns a parser instance for the file extension, or raises UnsupportedFileType.
    """
    global _BUILTINS_READY
    if not _BUILTINS_READY:
        _ensure_builtins_loaded()
        _BUILTINS_READY = True

    ext = path.suffix.lower()
    parser = _REGISTRY.get(ext)
    if not parser:
        raise UnsupportedFileType(f"No parser registered for extension '{ext}'.")
    return parser


def parse_path(path: Path) -> ParseResult:
    """
    Convenience: find a parser for the file path and run it.
    """
    parser = get_parser_for_path(path)
    return parser.parse_path(path)


# Re-export exceptions & types for convenience
__all__ = [
    "TextParser",
    "ParseResult",
    "UnsupportedFileType",
    "DependencyMissing",
    "ParseError",
    "available_parsers",
    "get_parser_for_path",
    "parse_path",
]
