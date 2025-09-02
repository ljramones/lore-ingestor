# lore_ingest/parsers/txt_md.py
from __future__ import annotations

from pathlib import Path
from typing import Dict, Any

from .base import TextParser, ParseResult
from ..normalize import detect_encoding


class TxtMdParser(TextParser):
    exts = {".txt", ".md"}

    def parse_path(self, path: Path) -> ParseResult:
        raw = path.read_bytes()
        enc = detect_encoding(raw)
        text = raw.decode(enc, errors="replace")
        meta: Dict[str, Any] = {
            "parser": "txtmd",
            "encoding": enc,
            "bytes": len(raw),
            "ext": path.suffix.lower(),
            "filename": path.name,
        }
        return ParseResult(raw=raw, text=text, meta=meta)


PARSER = TxtMdParser()
