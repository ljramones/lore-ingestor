# lore_ingest/parsers/docx.py
from __future__ import annotations

from pathlib import Path
from typing import Dict, Any

from .base import TextParser, ParseResult, DependencyMissing, ParseError


def _require_docx2txt():
    try:
        import docx2txt  # type: ignore
        return docx2txt
    except Exception as e:
        raise DependencyMissing("docx2txt is required for DOCX parsing. Install with: pip install docx2txt") from e


class DocxParser(TextParser):
    exts = {".docx"}

    def parse_path(self, path: Path) -> ParseResult:
        docx2txt = _require_docx2txt()
        raw = path.read_bytes()
        try:
            # docx2txt reads from path and returns plain text
            text = docx2txt.process(path.as_posix()) or ""
            meta: Dict[str, Any] = {
                "parser": "docx",
                "bytes": len(raw),
                "filename": path.name,
            }
            return ParseResult(raw=raw, text=text, meta=meta)
        except Exception as e:
            raise ParseError(f"Failed to parse DOCX: {path.name}") from e


PARSER = DocxParser()
