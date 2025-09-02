# lore_ingest/persist.py
from __future__ import annotations

import json
import sqlite3
import uuid
import hashlib
from typing import Any, Dict, Iterable, List, Optional, Tuple

# --- Connection helper -----------------------------------------------------

def open_db(db_path: str, *, check_same_thread: bool = False) -> sqlite3.Connection:
    """
    Open a SQLite database with sane defaults for this service.
    """
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


# --- DDL management --------------------------------------------------------

def _column_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row["name"] == col for row in cur.fetchall())


def ensure_ingest_columns_and_tables(conn: sqlite3.Connection) -> None:
    """
    Create/patch the minimal tables/indexes for the ingestor.
    Safe to call repeatedly.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS work (
          id            TEXT PRIMARY KEY,
          title         TEXT,
          author        TEXT,
          source        TEXT,
          license       TEXT,
          raw_text      BLOB,
          norm_text     TEXT,
          char_count    INTEGER,
          content_sha1  TEXT,
          ingest_run_id TEXT,
          created_at    TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );

        CREATE TABLE IF NOT EXISTS scene (
          id         TEXT PRIMARY KEY,
          work_id    TEXT NOT NULL,
          chapter_id TEXT,
          idx        INTEGER NOT NULL,
          char_start INTEGER,
          char_end   INTEGER,
          heading    TEXT,
          FOREIGN KEY (work_id) REFERENCES work(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS chunk (
          id          TEXT PRIMARY KEY,
          work_id     TEXT NOT NULL,
          scene_id    TEXT,
          idx         INTEGER NOT NULL,
          char_start  INTEGER,
          char_end    INTEGER,
          token_start INTEGER,
          token_end   INTEGER,
          text        TEXT NOT NULL,
          sha256      TEXT NOT NULL,
          FOREIGN KEY (work_id) REFERENCES work(id) ON DELETE CASCADE,
          FOREIGN KEY (scene_id) REFERENCES scene(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS ingest_run (
          id          TEXT PRIMARY KEY,
          created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
          params_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_work_title  ON work(title);
        CREATE INDEX IF NOT EXISTS idx_work_author ON work(author);
        CREATE INDEX IF NOT EXISTS idx_scene_work_idx ON scene(work_id, idx);

        CREATE INDEX IF NOT EXISTS idx_chunk_work_sha   ON chunk(work_id, sha256);
        CREATE INDEX IF NOT EXISTS idx_chunk_work_idx   ON chunk(work_id, idx);
        CREATE INDEX IF NOT EXISTS idx_chunk_work_scene ON chunk(work_id, scene_id, idx);
        CREATE INDEX IF NOT EXISTS idx_chunk_scene      ON chunk(scene_id);
        CREATE INDEX IF NOT EXISTS idx_chunk_work_span  ON chunk(work_id, char_start, char_end);
        """
    )

    # Backfill missing columns in older DBs (idempotent)
    if not _column_exists(conn, "work", "content_sha1"):
        conn.execute("ALTER TABLE work ADD COLUMN content_sha1 TEXT")
    if not _column_exists(conn, "work", "ingest_run_id"):
        conn.execute("ALTER TABLE work ADD COLUMN ingest_run_id TEXT")
    if not _column_exists(conn, "work", "char_count"):
        conn.execute("ALTER TABLE work ADD COLUMN char_count INTEGER")

    # Idempotency: enforce uniqueness on content_sha1 (NULLs allowed)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uniq_work_content_sha1 ON work(content_sha1)"
    )

    conn.commit()


# --- Lookups & helpers -----------------------------------------------------

def find_existing_work_by_digest_or_text(
    conn: sqlite3.Connection,
    *,
    content_sha1: Optional[str],
    norm_text: Optional[str] = None,
) -> Optional[str]:
    """
    Return an existing work.id if we detect a duplicate by digest (preferred),
    or by exact text match as a fallback (slower).
    """
    if content_sha1:
        row = conn.execute(
            "SELECT id FROM work WHERE content_sha1 = ? LIMIT 1",
            (content_sha1,),
        ).fetchone()
        if row:
            return row["id"]

    if norm_text is not None and len(norm_text) > 0:
        row = conn.execute(
            "SELECT id FROM work WHERE norm_text = ? LIMIT 1",
            (norm_text,),
        ).fetchone()
        if row:
            return row["id"]

    return None


def _uuid() -> str:
    return str(uuid.uuid4())


def _sha256_text(s: str) -> str:
    h = hashlib.sha256()
    h.update(s.encode("utf-8", errors="replace"))
    return h.hexdigest()


def _slice_safe(text: str, start: int, end: int) -> str:
    """Clamp and slice text safely."""
    n = len(text)
    s = max(0, min(int(start), n))
    e = max(s, min(int(end), n))
    return text[s:e]


# --- Persistence -----------------------------------------------------------

def persist_work_and_children(
    conn: sqlite3.Connection,
    *,
    title: Optional[str],
    author: Optional[str],
    source: Optional[str],
    license: Optional[str],
    raw_text: bytes,
    norm_text: str,
    scenes: Iterable[Any],   # objects with .idx/.start/.end[/heading] OR span-like
    chunks: Iterable[Any],   # objects with .idx/.start/.end[/text][/scene_id|scene_idx] OR span-like
    content_sha1: Optional[str],
    run_params: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Write ingest_run, work, scenes, and chunks into the DB transactionally.
    Robust to "span-only" objects (missing idx/text/scene refs).
    """
    # Create ingest_run record
    run_id = _uuid()
    conn.execute(
        "INSERT INTO ingest_run (id, params_json) VALUES (?, ?)",
        (run_id, json.dumps(run_params or {}, ensure_ascii=False)),
    )

    # Insert work
    work_id = _uuid()
    conn.execute(
        """
        INSERT INTO work (id, title, author, source, license, raw_text, norm_text, char_count, content_sha1, ingest_run_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            work_id,
            title,
            author,
            source,
            license,
            sqlite3.Binary(raw_text),
            norm_text,
            len(norm_text),
            content_sha1,
            run_id,
        ),
    )

    # --- Normalize scenes (allow missing idx) ---
    scenes_raw = list(scenes or [])
    scenes_norm: List[Dict[str, Any]] = []
    for i, s in enumerate(scenes_raw):
        s_idx = getattr(s, "idx", i)
        s_start = getattr(s, "start", 0)
        s_end = getattr(s, "end", s_start)
        s_heading = getattr(s, "heading", None)
        scenes_norm.append(
            {
                "idx": int(s_idx),
                "start": int(s_start),
                "end": int(s_end),
                "heading": s_heading,
            }
        )
    # Keep stable order by idx then start
    scenes_norm.sort(key=lambda x: (x["idx"], x["start"]))

    scene_id_by_idx: Dict[int, str] = {}
    if scenes_norm:
        scene_rows: List[Tuple[str, str, Optional[str], int, int, int, Optional[str]]] = []
        for s in scenes_norm:
            sid = _uuid()
            scene_id_by_idx[s["idx"]] = sid
            scene_rows.append((sid, work_id, None, s["idx"], s["start"], s["end"], s["heading"]))
        conn.executemany(
            """
            INSERT INTO scene (id, work_id, chapter_id, idx, char_start, char_end, heading)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            scene_rows,
        )

    # --- Normalize chunks (allow missing idx/text/scene refs) ---
    chunks_raw = list(chunks or [])
    chunk_rows: List[Tuple[str, str, Optional[str], int, int, int, Optional[int], Optional[int], str, str]] = []

    # Pre-build a simple lookup to map spans to scenes when needed
    def _scene_id_for_span(start: int) -> Optional[str]:
        for s in scenes_norm:
            if s["start"] <= start < s["end"]:
                return scene_id_by_idx.get(s["idx"])
        return None

    for i, c in enumerate(chunks_raw):
        # idx fallback → enumeration index
        c_idx = getattr(c, "idx", i)
        c_start = int(getattr(c, "start"))
        c_end = int(getattr(c, "end"))

        # Scene resolution: explicit scene_id > scene_idx > by-span
        scene_id: Optional[str] = getattr(c, "scene_id", None)
        if scene_id is None:
            c_scene_idx = getattr(c, "scene_idx", None)
            if isinstance(c_scene_idx, int) and c_scene_idx in scene_id_by_idx:
                scene_id = scene_id_by_idx[c_scene_idx]
            else:
                scene_id = _scene_id_for_span(c_start)

        # Text fallback → slice from work.norm_text
        text = getattr(c, "text", None)
        if text is None:
            text = _slice_safe(norm_text, c_start, c_end)

        cid = _uuid()
        chunk_rows.append(
            (
                cid,
                work_id,
                scene_id,
                int(c_idx),
                c_start,
                c_end,
                None,  # token_start
                None,  # token_end
                text,
                _sha256_text(text),
            )
        )

    if chunk_rows:
        conn.executemany(
            """
            INSERT INTO chunk (id, work_id, scene_id, idx, char_start, char_end, token_start, token_end, text, sha256)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            chunk_rows,
        )

    conn.commit()
    return work_id
