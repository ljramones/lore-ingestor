# lore_ingest/parsers/pdf.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
import logging

from lore_ingest.parsers.base import BaseParser, ParseResult, ParseError, DependencyMissing


class PdfParser(BaseParser):
    """
    Extract text per page and insert a visible page-break sentinel between pages.
    We use a token that will survive typical normalization: [[PAGE_BREAK]]
    """
    exts = {".pdf"}

    def parse_path(self, path: Path) -> ParseResult:
        # Silence noisy warnings from pypdf about malformed xref entries, etc.
        log = logging.getLogger("pypdf")
        prev_level = log.level
        log.setLevel(logging.ERROR)
        try:
            # Import here so we can surface a friendly dependency error
            try:
                from pypdf import PdfReader  # type: ignore
            except Exception as e:
                raise DependencyMissing(
                    "pypdf is required to ingest PDF files: pip install pypdf"
                ) from e

            if not path.exists():
                raise ParseError(f"File not found: {path}")

            raw = path.read_bytes()

            try:
                reader = PdfReader(path.as_posix())
                # Build the page texts in ONE pass (no duplicates)
                page_texts = [(p.extract_text() or "").rstrip() for p in reader.pages]
            except Exception as e:
                raise ParseError(f"Failed to parse PDF: {path.name}") from e

            # Sentinel that survives normalization and is easy to split on
            sentinel = "[[PAGE_BREAK]]"
            text = f"\n{sentinel}\n".join(page_texts)
            meta: Dict[str, Any] = {
                "parser": "pdf",
                "pages": len(page_texts),
                "page_break_token": sentinel,
            }
            return ParseResult(raw=raw, text=text, meta=meta)
        finally:
            log.setLevel(prev_level)


# Export singleton for discovery by lore_ingest.parsers.__init__
PARSER = PdfParser()
