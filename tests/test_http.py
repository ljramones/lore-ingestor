import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient


def init_db(db_path: Path) -> None:
    from tests.conftest import SCHEMA_SQL
    conn = sqlite3.connect(db_path.as_posix())
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def test_http_ingest_and_read(monkeypatch, tmp_path: Path):
    db = tmp_path / "api.db"
    init_db(db)
    monkeypatch.setenv("DB_PATH", db.as_posix())

    from service.http_app import app

    client = TestClient(app)

    r = client.get("/v1/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    doc = tmp_path / "doc.txt"
    source_text = "Title line\nSome content.\n\n\nSecond scene."
    doc.write_text(source_text, encoding="utf-8")

    r = client.post("/v1/ingest", json={"path": doc.as_posix(), "title": "The Doc"})
    assert r.status_code == 201, r.text
    payload = r.json()
    work_id = payload["work_id"]

    r = client.get(f"/v1/works/{work_id}")
    assert r.status_code == 200
    assert r.json()["title"] == "The Doc"

    r = client.get(f"/v1/works/{work_id}/scenes")
    assert r.status_code == 200
    assert isinstance(r.json(), list) and len(r.json()) >= 1

    r = client.get(f"/v1/works/{work_id}/chunks")
    assert r.status_code == 200
    assert isinstance(r.json(), list) and len(r.json()) >= 1

    conn = sqlite3.connect(db.as_posix())
    try:
        (norm_text,) = conn.execute(
            "SELECT norm_text FROM work WHERE id = ?", (work_id,)
        ).fetchone()
    finally:
        conn.close()

    start, end = 0, min(10, len(norm_text))
    r = client.get(f"/v1/works/{work_id}/slice", params={"start": start, "end": end})
    assert r.status_code == 200
    assert r.json()["text"] == norm_text[start:end]
