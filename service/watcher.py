# service/watcher.py
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Set

from lore_ingest.api import ingest_file
from lore_ingest.persist import open_db, ensure_ingest_columns_and_tables

ALLOWED_EXT: Set[str] = set((os.getenv("ALLOWED_EXT", ".txt,.md,.pdf,.docx")).split(","))
MAX_FILE_MB: int = int(os.getenv("MAX_FILE_MB", "32"))
EVENT_SINK: str = os.getenv("EVENT_SINK", "stdout")
DB_PATH: str = os.getenv("DB_PATH", "./tropes.db")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class WatchConfig:
    inbox: Path
    success_dir: Path
    fail_dir: Path
    db_path: str
    profile: Optional[str] = None


def _emit_event(payload: dict):
    if EVENT_SINK == "stdout":
        print(json.dumps(payload, ensure_ascii=False))
    # (webhook/redis/nats variants can be wired here if you set env + deps)


def _init_db(db_path: str):
    conn = open_db(db_path)
    ensure_ingest_columns_and_tables(conn)
    conn.close()


def worker_once(cfg: WatchConfig):
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    cfg.success_dir.mkdir(parents=True, exist_ok=True)
    cfg.fail_dir.mkdir(parents=True, exist_ok=True)

    for p in sorted(cfg.inbox.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in ALLOWED_EXT:
            # move to fail with .err.json
            _move_fail_with_err(cfg, p, f"Unsupported file type {p.suffix}")
            continue
        if p.stat().st_size > MAX_FILE_MB * 1024 * 1024:
            _move_fail_with_err(cfg, p, "File too large")
            continue

        try:
            res = ingest_file(
                path=p.as_posix(),
                title=p.stem,
                author=None,
                db_path=cfg.db_path,
                profile=cfg.profile,
                run_params={"inbox": str(cfg.inbox), "ingestor": "watcher"},
            )
            # success → move to success with work_id prefix
            dst = cfg.success_dir / f"{res.work_id}__{p.name}"
            shutil.move(p.as_posix(), dst.as_posix())
            _emit_event(
                {
                    "type": "document.ingested",
                    "work_id": res.work_id,
                    "path": str(dst),
                    "title": p.stem,
                    "author": None,
                    "content_sha1": res.content_sha1,
                    "sizes": res.sizes,
                    "run_id": None,
                    "created_at": _now_iso(),
                }
            )
        except Exception as e:
            _move_fail_with_err(cfg, p, f"{e!s}")


def _move_fail_with_err(cfg: WatchConfig, path: Path, message: str):
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dst = cfg.fail_dir / f"{ts}__{path.name}"
    shutil.move(path.as_posix(), dst.as_posix())
    err = {"message": message, "stage": "watch", "created_at": _now_iso()}
    err_path = dst.with_suffix(dst.suffix + ".err.json")
    err_path.write_text(json.dumps(err, ensure_ascii=False, indent=2), encoding="utf-8")


def run_watcher(
    *,
    inbox: Path,
    success_dir: Path,
    fail_dir: Path,
    db_path: str,
    profile: Optional[str] = None,
    poll_secs: float = 1.0,
):
    os.makedirs(inbox, exist_ok=True)
    os.makedirs(success_dir, exist_ok=True)
    os.makedirs(fail_dir, exist_ok=True)
    _init_db(db_path)

    cfg = WatchConfig(inbox=inbox, success_dir=success_dir, fail_dir=fail_dir, db_path=db_path, profile=profile or os.getenv("INGEST_PROFILE"))

    print(f"[watcher] inbox={inbox} success={success_dir} fail={fail_dir} db={db_path} profile={cfg.profile or 'default'}")
    while True:
        worker_once(cfg)
        time.sleep(poll_secs)


if __name__ == "__main__":
    run_watcher(
        inbox=Path(os.getenv("INBOX", "./inbox")),
        success_dir=Path(os.getenv("SUCCESS_DIR", "./success")),
        fail_dir=Path(os.getenv("FAIL_DIR", "./fail")),
        db_path=os.getenv("DB_PATH", "./tropes.db"),
        profile=os.getenv("INGEST_PROFILE"),
    )
