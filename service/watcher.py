# service/watcher.py
from __future__ import annotations

import os
import shutil
import time
import threading
import random
import queue
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Set, Any

from lore_ingest.api import ingest_file
from lore_ingest.events import build_ingested_event, build_failed_event, emit_async
from lore_ingest.pushgw import push_ingest


# ---------------- utils ----------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def json_dumps(o: Any) -> str:
    import json
    return json.dumps(o, ensure_ascii=False, indent=2)


def _is_ignorable(name: str) -> bool:
    n = name.lower()
    # dotfiles, temp/partial downloads, office locks, temp saves
    if n.startswith(".") or n.startswith("._"):
        return True
    if n.startswith("~$") or n.startswith(".~lock"):
        return True
    if n.endswith(".tmp") or n.endswith(".crdownload") or n.endswith(".partial"):
        return True
    return False


def _unique_move(dst_dir: Path, src: Path, prefix: str = "") -> Path:
    """
    Atomic-ish move. If target exists, append -1, -2, ...
    """
    dst_dir.mkdir(parents=True, exist_ok=True)
    base = f"{prefix}{src.name}"
    target = dst_dir / base
    if not target.exists():
        shutil.move(src.as_posix(), target.as_posix())
        return target
    stem = target.stem
    suf = target.suffix
    i = 1
    while True:
        alt = dst_dir / f"{stem}-{i}{suf}"
        if not alt.exists():
            shutil.move(src.as_posix(), alt.as_posix())
            return alt
        i += 1


def _write_fail_err(fail_dir: Path, src: Path, reason: str, stage: str) -> Path:
    ts = int(time.time())
    moved = _unique_move(fail_dir, src, prefix=f"{ts}__")
    (moved.with_suffix(moved.suffix + ".err.json")).write_text(
        json_dumps({"message": reason, "stage": stage, "created_at": utc_now_iso()}),
        encoding="utf-8",
    )
    return moved


# ---------------- config ----------------

@dataclass
class WatcherConfig:
    inbox: Path
    success_dir: Path
    fail_dir: Path
    db_path: str
    allowed_ext: Set[str]
    max_file_mb: int
    profile: Optional[str] = None
    workers: int = 2
    max_queue: int = 100
    stable_ms: int = 750
    poll_seconds: float = 1.0
    retries: int = 2
    backoff_base_ms: int = 250
    recursive: bool = False


def load_config_from_env() -> WatcherConfig:
    inbox = Path(os.getenv("INBOX", "./inbox"))
    success_dir = Path(os.getenv("SUCCESS_DIR", "./success"))
    fail_dir = Path(os.getenv("FAIL_DIR", "./fail"))
    db_path = os.getenv("DB_PATH", "./tropes.db")

    allowed_ext = {e.strip().lower() for e in os.getenv("ALLOWED_EXT", ".txt,.md,.pdf,.docx").split(",") if e.strip()}
    max_file_mb = int(os.getenv("MAX_FILE_MB", "20"))
    profile = os.getenv("INGEST_PROFILE") or None

    workers = max(1, int(os.getenv("WATCH_WORKERS", "2")))
    max_queue = max(1, int(os.getenv("WATCH_MAX_QUEUE", "100")))
    stable_ms = max(0, int(os.getenv("WATCH_STABLE_MS", "750")))
    poll_seconds = float(os.getenv("WATCH_POLL_SECONDS", "1.0"))
    retries = max(0, int(os.getenv("WATCH_RETRIES", "2")))
    backoff_base_ms = max(1, int(os.getenv("WATCH_BACKOFF_BASE_MS", "250")))
    recursive = os.getenv("WATCH_RECURSIVE", "false").lower() in {"1", "true", "yes"}

    for p in (inbox, success_dir, fail_dir):
        p.mkdir(parents=True, exist_ok=True)

    return WatcherConfig(
        inbox=inbox,
        success_dir=success_dir,
        fail_dir=fail_dir,
        db_path=db_path,
        allowed_ext=allowed_ext,
        max_file_mb=max_file_mb,
        profile=profile,
        workers=workers,
        max_queue=max_queue,
        stable_ms=stable_ms,
        poll_seconds=poll_seconds,
        retries=retries,
        backoff_base_ms=backoff_base_ms,
        recursive=recursive,
    )


# ---------------- work items ----------------

@dataclass
class WorkItem:
    path: Path
    attempt: int = 0  # 0-based


# ---------------- main loop (dispatcher + workers) ----------------

def run_watcher(
    *,
    inbox: Path,
    success_dir: Path,
    fail_dir: Path,
    db_path: str,
    profile: Optional[str] = None,
    poll_seconds: float | None = None,  # optional override
) -> None:
    # Build config (env-driven) and allow poll override if provided by caller
    cfg = load_config_from_env()
    # Respect explicit args for compatibility
    cfg.inbox = inbox
    cfg.success_dir = success_dir
    cfg.fail_dir = fail_dir
    cfg.db_path = db_path
    if profile is not None:
        cfg.profile = profile
    if poll_seconds is not None:
        cfg.poll_seconds = poll_seconds

    print(
        f"[watcher] watching {cfg.inbox} (recursive={cfg.recursive}) "
        f"→ success={cfg.success_dir} fail={cfg.fail_dir} profile={cfg.profile or 'default'} "
        f"workers={cfg.workers} queue={cfg.max_queue}"
    )

    q: queue.Queue[WorkItem] = queue.Queue(maxsize=cfg.max_queue)
    stop = threading.Event()
    seen: set[str] = set()

    def enqueue_candidate(p: Path):
        """Check filters + stability, then enqueue if space is available."""
        name = p.name
        if _is_ignorable(name):
            return

        # extension filter
        if p.suffix.lower() not in cfg.allowed_ext:
            # Precheck fail path here: move and emit
            reason = f"Unsupported extension: {p.suffix}"
            _write_fail_err(cfg.fail_dir, p, reason, stage="precheck")
            print(f"[watcher] fail (unsupported) {name}")
            emit_async(build_failed_event(source_path=p.as_posix(), title=None, author=None, reason=reason, stage="precheck", profile=cfg.profile))
            push_ingest("fail", duration_s=None, extra_labels={"source": "watcher"})
            return

        # stat & stability
        try:
            st1 = p.stat()
        except FileNotFoundError:
            return
        size1 = st1.st_size
        key = f"{p.as_posix()}:{st1.st_mtime_ns}"
        if key in seen:
            return

        # oversized precheck
        if size1 > cfg.max_file_mb * 1024 * 1024:
            reason = f"File too large (> {cfg.max_file_mb} MB)"
            _write_fail_err(cfg.fail_dir, p, reason, stage="precheck")
            print(f"[watcher] fail (oversized) {name}")
            emit_async(build_failed_event(source_path=p.as_posix(), title=None, author=None, reason=reason, stage="precheck", profile=cfg.profile))
            push_ingest("fail", duration_s=None, extra_labels={"source": "watcher"})
            return

        # stability check (size unchanged over STABLE_MS)
        if cfg.stable_ms > 0:
            time.sleep(cfg.stable_ms / 1000.0)
            try:
                st2 = p.stat()
            except FileNotFoundError:
                return
            if st2.st_size != size1:
                return  # not stable yet

        # enqueue with backpressure (block briefly if full)
        try:
            q.put(WorkItem(path=p), timeout=1.0)
            seen.add(key)
        except queue.Full:
            # Backpressure: skip for now; next scan will try again
            return

    # Worker function with retry/backoff
    def worker_loop(wid: int):
        while not stop.is_set():
            try:
                item = q.get(timeout=0.5)
            except queue.Empty:
                continue

            p = item.path
            try:
                # Re-check existence and basic filters quickly (race safety)
                if not p.exists() or not p.is_file():
                    q.task_done()
                    continue

                # Ingest
                try:
                    res = ingest_file(
                        path=p.as_posix(),
                        title=None,
                        author=None,
                        db_path=cfg.db_path,
                        profile=cfg.profile,
                        run_params={"invoked_by": "watcher"},
                    )
                    # move to success
                    dst = _unique_move(cfg.success_dir, p, prefix=f"{res.work_id}__")
                    print(f"[watcher] ok  work_id={res.work_id} -> {dst.name}")
                    push_ingest("ok", duration_s=None, extra_labels={"source": "watcher"})
                    emit_async(
                        build_ingested_event(
                            db_path=cfg.db_path,
                            work_id=res.work_id,
                            source_path=p.as_posix(),
                            title=None,
                            author=None,
                            content_sha1=res.content_sha1,
                            sizes=res.sizes,
                            profile=cfg.profile,
                            extra={"moved_to": dst.as_posix()},
                        )
                    )
                except Exception as e:
                    # Retry with exponential backoff + jitter
                    if item.attempt < cfg.retries:
                        backoff = (cfg.backoff_base_ms * (2 ** item.attempt)) / 1000.0
                        backoff *= (0.8 + 0.4 * random.random())  # jitter 80–120%
                        print(f"[watcher] retry {item.attempt+1}/{cfg.retries} after {backoff:.2f}s for {p.name} err={e}")
                        time.sleep(backoff)
                        try:
                            q.put_nowait(WorkItem(path=p, attempt=item.attempt + 1))
                        except queue.Full:
                            # If queue is saturated, drop to next scan; file still in inbox
                            pass
                    else:
                        reason = str(e)
                        failed_path = _write_fail_err(cfg.fail_dir, p, reason, stage="ingest")
                        print(f"[watcher] fail (ingest) {p.name} -> {failed_path.name} reason={reason}")
                        push_ingest("fail", duration_s=None, extra_labels={"source": "watcher"})
                        emit_async(
                            build_failed_event(
                                source_path=p.as_posix(),
                                title=None,
                                author=None,
                                reason=reason,
                                stage="ingest",
                                profile=cfg.profile,
                            )
                        )
                finally:
                    q.task_done()
            except Exception as e:
                # Defensive: ensure task_done even on unexpected errors
                q.task_done()
                print(f"[watcher] worker-{wid} unexpected error: {e}")

    # Start workers
    threads = [threading.Thread(target=worker_loop, args=(i,), daemon=True) for i in range(cfg.workers)]
    for t in threads:
        t.start()

    # Dispatcher loop: scan and enqueue
    while not stop.is_set():
        try:
            if cfg.recursive:
                for p in cfg.inbox.rglob("*"):
                    if p.is_file():
                        enqueue_candidate(p)
            else:
                for p in cfg.inbox.iterdir():
                    if p.is_file():
                        enqueue_candidate(p)
        except FileNotFoundError:
            # inbox deleted? recreate and continue
            cfg.inbox.mkdir(parents=True, exist_ok=True)
        time.sleep(cfg.poll_seconds)
