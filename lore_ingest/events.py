# lore_ingest/events.py
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Hard deps
import requests
import sqlite3


# -------------------- time --------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -------------------- sink base --------------------

class EventSink:
    name: str = "base"
    def emit(self, payload: Dict[str, Any]) -> None:  # pragma: no cover
        raise NotImplementedError


# -------------------- sinks --------------------

class StdoutSink(EventSink):
    name = "stdout"
    def emit(self, payload: Dict[str, Any]) -> None:
        # Print compact JSON to stdout (docker logs will pick this up)
        print(json.dumps(payload, ensure_ascii=False))


class HttpSink(EventSink):
    name = "http"
    def __init__(self, url: str, timeout: float = 5.0):
        self.url = url
        self.timeout = timeout
    def emit(self, payload: Dict[str, Any]) -> None:
        try:
            requests.post(self.url, json=payload, timeout=self.timeout)
        except Exception:
            # best-effort; never raise to caller
            pass


class RedisSink(EventSink):
    name = "redis"
    def __init__(self, url: str, list_name: str = "ingest_events"):
        try:
            import redis  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "redis Python package is required for Redis sink. Add 'redis>=5' to requirements."
            ) from e
        self.client = redis.from_url(url)
        self.list_name = list_name
    def emit(self, payload: Dict[str, Any]) -> None:
        try:
            self.client.rpush(self.list_name, json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass


class NatsSink(EventSink):
    name = "nats"
    def __init__(self, url: str, subject: str = "ingest.events"):
        self.url = url
        self.subject = subject
    def emit(self, payload: Dict[str, Any]) -> None:
        # Light one-shot publish (fine for low QPS)
        try:
            import asyncio  # type: ignore
            import nats     # type: ignore
            async def _pub():
                nc = await nats.connect(self.url)
                try:
                    await nc.publish(self.subject, json.dumps(payload).encode("utf-8"))
                finally:
                    await nc.drain()
            asyncio.run(_pub())
        except Exception:
            pass


# -------------------- factory/manager --------------------

def get_sinks_from_env() -> List[EventSink]:
    """
    EMIT_SINK: comma-separated list: stdout | http | redis | nats
      - EMIT_HTTP_URL
      - EMIT_REDIS_URL, EMIT_REDIS_LIST
      - EMIT_NATS_URL,  EMIT_NATS_SUBJECT
    Empty/none/off/false -> no sinks.
    """
    raw = os.getenv("EMIT_SINK", "stdout").strip()
    if not raw or raw.lower() in {"none", "off", "false"}:
        return []
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    sinks: List[EventSink] = []
    for name in parts:
        if name == "stdout":
            sinks.append(StdoutSink())
        elif name == "http":
            url = os.getenv("EMIT_HTTP_URL", "").strip()
            if url:
                sinks.append(HttpSink(url))
        elif name == "redis":
            url = os.getenv("EMIT_REDIS_URL", "redis://redis:6379/0").strip()
            list_name = os.getenv("EMIT_REDIS_LIST", "ingest_events").strip()
            sinks.append(RedisSink(url, list_name))
        elif name == "nats":
            url = os.getenv("EMIT_NATS_URL", "nats://nats:4222").strip()
            subject = os.getenv("EMIT_NATS_SUBJECT", "ingest.events").strip()
            sinks.append(NatsSink(url, subject))
        # unknown names are ignored
    return sinks


@dataclass
class EventConfig:
    sinks: List[EventSink]


_manager: Optional[EventConfig] = None


def event_manager() -> EventConfig:
    global _manager
    if _manager is None:
        _manager = EventConfig(sinks=get_sinks_from_env())
    return _manager


def reload_sinks() -> None:
    """Re-read env and rebuild sinks (useful for tests)."""
    global _manager
    _manager = EventConfig(sinks=get_sinks_from_env())


def emit_async(payload: Dict[str, Any]) -> None:
    """Fan-out to all sinks without blocking the caller."""
    cfg = event_manager()
    if not cfg.sinks:
        return
    def _run():
        for s in cfg.sinks:
            try:
                s.emit(payload)
            except Exception:
                # sink errors are non-fatal by design
                pass
    threading.Thread(target=_run, daemon=True).start()


# -------------------- payload builders --------------------

def build_ingested_event(
    *,
    db_path: str,
    work_id: str,
    source_path: str,
    title: Optional[str],
    author: Optional[str],
    content_sha1: Optional[str],
    sizes: Dict[str, int],
    profile: Optional[str],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    document.ingested payload. Best-effort inclusion of run_id if present on `work`.
    """
    payload: Dict[str, Any] = {
        "type": "document.ingested",
        "work_id": work_id,
        "path": source_path,
        "title": title,
        "author": author,
        "content_sha1": content_sha1,
        "sizes": sizes,
        "profile": profile,
        "created_at": utc_now_iso(),
    }

    # Try to include ingest_run_id if present (schema may or may not have it)
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT ingest_run_id FROM work WHERE id = ?", (work_id,)).fetchone()
            if row and "ingest_run_id" in row.keys() and row["ingest_run_id"]:
                payload["run_id"] = row["ingest_run_id"]
        finally:
            conn.close()
    except Exception:
        pass

    if extra:
        payload.update(extra)
    return payload


def build_failed_event(
    *,
    source_path: str,
    title: Optional[str],
    author: Optional[str],
    reason: str,
    stage: str,
    profile: Optional[str],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    document.failed payload for parse errors, oversized files, unsupported types, etc.
    """
    payload: Dict[str, Any] = {
        "type": "document.failed",
        "path": source_path,
        "title": title,
        "author": author,
        "reason": reason,
        "stage": stage,
        "profile": profile,
        "created_at": utc_now_iso(),
    }
    if extra:
        payload.update(extra)
    return payload


# -------------------- tiny debug helper --------------------

def sinks_summary() -> Dict[str, Any]:
    """Return a quick snapshot of configured sinks (for debugging)."""
    cfg = event_manager()
    return {
        "count": len(cfg.sinks),
        "sinks": [s.name for s in cfg.sinks],
        "env": {
            "EMIT_SINK": os.getenv("EMIT_SINK", ""),
            "EMIT_HTTP_URL": os.getenv("EMIT_HTTP_URL", ""),
            "EMIT_REDIS_URL": os.getenv("EMIT_REDIS_URL", ""),
            "EMIT_REDIS_LIST": os.getenv("EMIT_REDIS_LIST", ""),
            "EMIT_NATS_URL": os.getenv("EMIT_NATS_URL", ""),
            "EMIT_NATS_SUBJECT": os.getenv("EMIT_NATS_SUBJECT", ""),
        },
    }
