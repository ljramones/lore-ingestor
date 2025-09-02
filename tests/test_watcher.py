import json
import queue
import sqlite3
import threading
import time
from pathlib import Path


def init_db(db_path: Path) -> None:
    from tests.conftest import SCHEMA_SQL
    conn = sqlite3.connect(db_path.as_posix())
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def test_worker_loop_moves_to_success_and_updates_db(monkeypatch, tmp_path: Path):
    db = tmp_path / "watcher.db"
    inbox = tmp_path / "inbox"
    success = tmp_path / "success"
    fail = tmp_path / "fail"
    inbox.mkdir()
    success.mkdir()
    fail.mkdir()
    init_db(db)

    monkeypatch.setenv("DB_PATH", db.as_posix())
    monkeypatch.setenv("INBOX", inbox.as_posix())
    monkeypatch.setenv("SUCCESS_DIR", success.as_posix())
    monkeypatch.setenv("FAIL_DIR", fail.as_posix())
    monkeypatch.setenv("ALLOWED_EXT", ".txt,.md,.pdf,.docx")

    import importlib
    import service.watcher as watcher
    watcher = importlib.reload(watcher)

    doc = inbox / "story.txt"
    doc.write_text("A scene.\n\n\nAnother scene.", encoding="utf-8")

    q: "queue.Queue[Path]" = queue.Queue()
    stop_flag = threading.Event()
    t = threading.Thread(target=watcher.worker_loop, args=(q, stop_flag), daemon=True)
    t.start()

    q.put(doc)

    deadline = time.time() + 10.0
    found_success = False
    while time.time() < deadline:
        if any(success.iterdir()):
            found_success = True
            break
        time.sleep(0.1)

    stop_flag.set()
    t.join(timeout=2.0)

    assert found_success, "Processed file was not moved to success/"

    conn = sqlite3.connect(db.as_posix())
    try:
        w = conn.execute("SELECT COUNT(*) FROM work").fetchone()[0]
        s = conn.execute("SELECT COUNT(*) FROM scene").fetchone()[0]
        c = conn.execute("SELECT COUNT(*) FROM chunk").fetchone()[0]
        assert w == 1 and s >= 1 and c >= 1
    finally:
        conn.close()

    assert not any(fail.iterdir()), "Fail directory should be empty"

