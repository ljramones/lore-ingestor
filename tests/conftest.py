# tests/conftest.py
import sqlite3
import sys
from pathlib import Path

import pytest

# Add repo root to sys.path so `import lore_ingest` works
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS work (
  id TEXT PRIMARY KEY,
  title TEXT,
  author TEXT,
  source TEXT,
  license TEXT,
  raw_text BLOB,
  norm_text TEXT,
  char_count INTEGER,
  content_sha1 TEXT,
  ingest_run_id TEXT,
  created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS scene (
  id TEXT PRIMARY KEY,
  work_id TEXT NOT NULL,
  chapter_id TEXT,
  idx INTEGER NOT NULL,
  char_start INTEGER,
  char_end INTEGER,
  heading TEXT
);

CREATE TABLE IF NOT EXISTS chunk (
  id TEXT PRIMARY KEY,
  work_id TEXT NOT NULL,
  scene_id TEXT,
  idx INTEGER NOT NULL,
  char_start INTEGER,
  char_end INTEGER,
  token_start INTEGER,
  token_end INTEGER,
  text TEXT NOT NULL,
  sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_run (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  params_json TEXT NOT NULL
);
"""

@pytest.fixture
def init_db():
    def _init(db_path: Path):
        conn = sqlite3.connect(db_path.as_posix())
        try:
            conn.executescript(SCHEMA_SQL)
            conn.commit()
        finally:
            conn.close()
    return _init
