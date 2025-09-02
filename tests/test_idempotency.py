from __future__ import annotations
import importlib
import os
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from lore_ingest.persist import open_db, ensure_ingest_columns_and_tables
from lore_ingest.api import ingest_file


def _init_db(db_path: Path):
    conn = open_db(db_path.as_posix())
    ensure_ingest_columns_and_tables(conn)
    conn.close()


def _count_works(db_path: Path) -> int:
    with sqlite3.connect(db_path.as_posix()) as conn:
        return conn.execute("SELECT COUNT(*) FROM work").fetchone()[0]


def test_idempotent_ingest_library(tmp_path: Path, monkeypatch):
    db = tmp_path / "lib.db"
    _init_db(db)

    # two identical files → one work in DB
    f = tmp_path / "doc.txt"
    f.write_text("Hello\n\nWorld", encoding="utf-8")

    res1 = ingest_file(path=f.as_posix(), db_path=db.as_posix())
    res2 = ingest_file(path=f.as_posix(), db_path=db.as_posix())

    assert res1.content_sha1 == res2.content_sha1
    assert _count_works(db) == 1


def test_idempotent_ingest_http(tmp_path: Path, monkeypatch):
    db = tmp_path / "api.db"
    _init_db(db)
    monkeypatch.setenv("DB_PATH", db.as_posix())
    monkeypatch.setenv("EVENT_SINK", "stdout")  # keep event emission simple

    # import AFTER setting env so http_app picks up the DB_PATH
    http_app = importlib.import_module("service.http_app")
    importlib.reload(http_app)

    client = TestClient(http_app.app)

    f = tmp_path / "doc.txt"
    f.write_text("Title line\nSome content.\n\n\nSecond scene.", encoding="utf-8")

    # 1st ingest
    r1 = client.post("/v1/ingest", json={"path": f.as_posix(), "title": "Sample"})
    assert r1.status_code == 201
    j1 = r1.json()
    assert "work_id" in j1 and "content_sha1" in j1

    # 2nd ingest of same file
    r2 = client.post("/v1/ingest", json={"path": f.as_posix(), "title": "Sample"})
    assert r2.status_code == 201
    j2 = r2.json()
    assert j1["content_sha1"] == j2["content_sha1"]

    # DB should still have a single work
    works = client.get("/v1/works?limit=10").json()
    assert isinstance(works, list)
    assert len(works) == 1
