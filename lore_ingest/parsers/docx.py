# lore_ingest/parsers/docx.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Any, List

from lore_ingest.parsers.base import BaseParser, ParseResult, DependencyMissing, ParseError


def _strip_headers_footers_heuristic(text: str) -> str:
    """
    Lightweight heuristic to drop common header/footer noise:
    - pure page numbers ("12", "Page 12", "12 / 34")
    - MS Office temp markers
    - repeated single-line banner equal to filename (occurs often with exports)
    """
    lines = text.splitlines()
    out: List[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            out.append(ln)
            continue
        # page number patterns
        if s.isdigit():
            continue
        if s.lower().startswith("page ") and s[5:].strip().isdigit():
            continue
        if "/" in s:
            parts = [p.strip() for p in s.split("/")]
            if len(parts) == 2 and all(p.isdigit() for p in parts):
                continue
        if s.lower().startswith("header") or s.lower().startswith("footer"):
            continue
        out.append(ln)
    return "\n".join(out)


class DocxParser(BaseParser):
    exts = {".docx"}

    def parse_path(self, path: Path) -> ParseResult:
        # Prefer docx2txt to avoid heavy deps
        try:
            import docx2txt  # type: ignore
        except Exception as e:
            raise DependencyMissing("docx2txt is required for DOCX parsing. pip install docx2txt") from e

        if not path.exists():
            raise ParseError(f"File not found: {path}")

        raw = path.read_bytes()
        warnings: List[str] = []
        try:
            text = docx2txt.process(path.as_posix()) or ""
            # optional header/footer strip (heuristic)
            if os.getenv("DOCX_STRIP_HF", "false").lower() in {"1", "true", "yes"}:
                text = _strip_headers_footers_heuristic(text)
        except Exception as e:
            raise ParseError(f"Failed to parse DOCX: {path.name}") from e

        # If extraction suspiciously empty, surface a warning
        if not text.strip():
            warnings.append("docx2txt returned empty text")

        meta: Dict[str, Any] = {
            "parser": "docx",
            "bytes": len(raw),
            "warnings": warnings or None,
        }
        return ParseResult(raw=raw, text=text, meta=meta)


PARSER = DocxParser()
