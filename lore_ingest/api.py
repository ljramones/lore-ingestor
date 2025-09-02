# lore_ingest/api.py
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from lore_ingest.normalize import normalize_text
from lore_ingest.segment import segment_to_scenes
from lore_ingest.chunk import make_chunks
from lore_ingest.persist import (
    open_db,
    ensure_ingest_columns_and_tables,
    find_existing_work_by_digest_or_text,
    persist_work_and_children,
)
# parse_file returns a ParseResult object with .raw, .text, .meta
from lore_ingest.parsers import parse_path as parse_file, available_parsers  # noqa: F401


@dataclass
class IngestResult:
    work_id: str
    content_sha1: Optional[str]
    sizes: Dict[str, int]


def compute_sha1(raw: bytes) -> str:
    h = hashlib.sha1()
    h.update(raw)
    return h.hexdigest()


def _sizes_for_work(conn, work_id: str) -> Dict[str, int]:
    row = conn.execute("SELECT char_count FROM work WHERE id = ?", (work_id,)).fetchone()
    chars = int(row["char_count"]) if row and row["char_count"] is not None else 0
    scenes = conn.execute("SELECT COUNT(*) FROM scene WHERE work_id = ?", (work_id,)).fetchone()[0]
    chunks = conn.execute("SELECT COUNT(*) FROM chunk WHERE work_id = ?", (work_id,)).fetchone()[0]
    return {"chars": chars, "scenes": scenes, "chunks": chunks}


def ingest_file(
    *,
    path: str,
    title: Optional[str] = None,
    author: Optional[str] = None,
    db_path: str = "./tropes.db",
    window_chars: int = 512,
    stride_chars: int = 384,
    run_params: Optional[Dict] = None,
    profile: Optional[str] = None,
) -> IngestResult:
    """
    Ingest a single file path into the DB. Idempotent via content_sha1 of raw bytes.
    """
    # Parse file -> ParseResult(raw, text, meta)
    pr = parse_file(Path(path))
    raw: bytes = pr.raw
    extracted: str = pr.text
    meta: Dict = getattr(pr, "meta", {}) or {}

    content_sha1 = compute_sha1(raw)

    # Normalize
    norm = normalize_text(extracted)

    # DB setup
    conn = open_db(db_path)
    ensure_ingest_columns_and_tables(conn)

    # Idempotency: skip if same digest already exists
    existing = find_existing_work_by_digest_or_text(conn, content_sha1=content_sha1, norm_text=None)
    if existing:
        return IngestResult(work_id=existing, content_sha1=content_sha1, sizes=_sizes_for_work(conn, existing))

    # Segment + chunk (profile-aware)
    scenes = segment_to_scenes(norm, profile=profile)
    chunks = make_chunks(norm, scenes, window_chars=window_chars, stride_chars=stride_chars, profile=profile)

    # Persist
    run_meta = {
        "profile": profile or "default",
        "parser": meta.get("parser"),
        "encoding": meta.get("encoding"),
        "source_ext": os.path.splitext(path)[1].lower(),
    }
    if run_params:
        run_meta.update(run_params)

    work_id = persist_work_and_children(
        conn,
        title=title,
        author=author,
        source=os.path.basename(path) or meta.get("source") or path,
        license=None,
        raw_text=raw,
        norm_text=norm,
        scenes=scenes,
        chunks=chunks,
        content_sha1=content_sha1,
        run_params=run_meta,
    )

    sizes = {"chars": len(norm), "scenes": len(scenes), "chunks": len(chunks)}
    return IngestResult(work_id=work_id, content_sha1=content_sha1, sizes=sizes)


__all__ = [
    "ingest_file",
    "IngestResult",
    "available_parsers",
]

