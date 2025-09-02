# lore_ingest/normalize.py
from __future__ import annotations

import re

try:
    import chardet  # type: ignore
except Exception:
    chardet = None  # optional dependency


_NEWLINES_RE = re.compile(r"\r\n?")  # CRLF or CR -> LF
_NULLS_RE = re.compile(r"\x00")


def detect_encoding(raw: bytes) -> str:
    """
    Best-effort text encoding detection. Defaults to 'utf-8' with fallback to cp1252.
    """
    if not raw:
        return "utf-8"
    if chardet is not None:
        try:
            guess = chardet.detect(raw)  # {'encoding': 'utf-8', 'confidence': 0.99, ...}
            enc = (guess.get("encoding") or "").lower()
            if enc:
                return enc
        except Exception:
            pass
    # Heuristics: try utf-8 then cp1252
    try:
        raw.decode("utf-8")
        return "utf-8"
    except Exception:
        return "cp1252"


def normalize_text(s: str) -> str:
    """
    Minimal normalization that preserves character offsets:
    - Convert CRLF/CR -> LF
    - Strip NULs
    (We avoid “smart quote” substitutions to keep exact content stable.)
    """
    s = _NEWLINES_RE.sub("\n", s)
    s = _NULLS_RE.sub("", s)
    return s
