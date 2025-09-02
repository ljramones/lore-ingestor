# lore_ingest/temporal.py
from __future__ import annotations

from pathlib import Path
from typing import Optional, Dict

from lore_ingest.api import ingest_file, IngestResult


def ingest_activity(
    path: str,
    *,
    title: Optional[str] = None,
    author: Optional[str] = None,
    db_path: str = "./tropes.db",
    window_chars: int = 512,
    stride_chars: int = 384,
    run_params: Optional[Dict] = None,
) -> IngestResult:
    """Temporal-friendly activity function (no Temporal imports required)."""
    return ingest_file(
        path=Path(path).as_posix(),
        title=title,
        author=author,
        db_path=db_path,
        window_chars=window_chars,
        stride_chars=stride_chars,
        run_params=(run_params or {}) | {"invoked_by": "temporal"},
    )
