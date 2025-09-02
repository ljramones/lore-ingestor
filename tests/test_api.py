import sqlite3
from pathlib import Path

from lore_ingest.api import ingest_file


def init_db(db_path: Path) -> None:
    from tests.conftest import SCHEMA_SQL  # reuse the same schema
    conn = sqlite3.connect(db_path.as_posix())
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def test_ingest_idempotent_and_counts(tmp_path: Path):
    db = tmp_path / "tropes.db"
    init_db(db)

    text = "CHAPTER I\nFirst scene line.\n\n\nSecond scene starts here."
    f = tmp_path / "work.txt"
    f.write_text(text, encoding="utf-8")

    res1 = ingest_file(path=f, db_path=db.as_posix())
    assert res1.work_id
    assert len(res1.content_sha1) == 40
    assert res1.scenes >= 1
    assert res1.chunks >= 1

    res2 = ingest_file(path=f, db_path=db.as_posix())
    assert res2.work_id == res1.work_id

    conn = sqlite3.connect(db.as_posix())
    try:
        w = conn.execute("SELECT COUNT(*) FROM work").fetchone()[0]
        s = conn.execute("SELECT COUNT(*) FROM scene").fetchone()[0]
        c = conn.execute("SELECT COUNT(*) FROM chunk").fetchone()[0]
        assert w == 1
        assert s >= 1
        assert c >= 1
    finally:
        conn.close()
