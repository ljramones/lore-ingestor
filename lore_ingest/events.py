# lore_ingest/events.py
from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol



class EventSink(Protocol):
    def emit(self, payload: Dict[str, Any]) -> None: ...


# ---------- Sinks ----------

class StdoutSink:
    def emit(self, payload: Dict[str, Any]) -> None:
        print(json.dumps(payload, ensure_ascii=False))


class WebhookSink:
    def __init__(self, url: str, auth: Optional[str] = None, timeout: float = 1.0):
        self.url = url
        self.auth = auth
        self.timeout = timeout
        try:
            import httpx  # type: ignore
        except Exception as e:
            raise RuntimeError("httpx is required for webhook events: pip install httpx") from e
        self._httpx = __import__("httpx")

    def emit(self, payload: Dict[str, Any]) -> None:
        headers = {"content-type": "application/json"}
        if self.auth:
            headers["authorization"] = self.auth
        try:
            self._httpx.post(self.url, json=payload, headers=headers, timeout=self.timeout)
        except Exception as e:
            print(json.dumps({"event": "event.emit.error", "sink": "webhook", "error": str(e)}), file=sys.stderr)


class RedisSink:
    def __init__(self, dsn: str, channel: str = "document.ingested"):
        try:
            import redis  # type: ignore
        except Exception as e:
            raise RuntimeError("redis is required for Redis events: pip install redis") from e
        self._redis = __import__("redis")
        self.client = self._redis.Redis.from_url(dsn)
        self.channel = channel

    def emit(self, payload: Dict[str, Any]) -> None:
        try:
            self.client.publish(self.channel, json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            print(json.dumps({"event": "event.emit.error", "sink": "redis", "error": str(e)}), file=sys.stderr)


class NatsSink:
    def __init__(self, url: str, subject: str = "document.ingested"):
        self.url = url
        self.subject = subject
        try:
            import nats  # type: ignore
        except Exception as e:
            raise RuntimeError("nats-py is required for NATS events: pip install nats-py") from e
        self._nats = __import__("nats")

    def emit(self, payload: Dict[str, Any]) -> None:
        # Lazy one-shot publish to keep it simple; fine for low QPS.
        async def _pub(url: str, subj: str, data: bytes):
            nc = await self._nats.connect(url)
            try:
                await nc.publish(subj, data)
            finally:
                await nc.flush()
                await nc.close()
        try:
            asyncio.run(_pub(self.url, self.subject, json.dumps(payload, ensure_ascii=False).encode("utf-8")))
        except Exception as e:
            print(json.dumps({"event": "event.emit.error", "sink": "nats", "error": str(e)}), file=sys.stderr)


# ---------- Emitter ----------

@dataclass
class EventConfig:
    kind: str = os.getenv("EVENT_SINK", "stdout").strip().lower()
    webhook_url: Optional[str] = os.getenv("EVENT_WEBHOOK_URL")
    webhook_auth: Optional[str] = os.getenv("EVENT_WEBHOOK_AUTH")  # e.g., "Bearer <token>"
    redis_dsn: Optional[str] = os.getenv("EVENT_REDIS_DSN")
    redis_channel: str = os.getenv("EVENT_REDIS_CHANNEL", "document.ingested")
    nats_url: str = os.getenv("EVENT_NATS_URL", "nats://127.0.0.1:4222")
    nats_subject: str = os.getenv("EVENT_NATS_SUBJECT", "document.ingested")


class EventEmitter:
    def __init__(self, sink: EventSink):
        self.sink = sink

    @classmethod
    def from_env(cls) -> "EventEmitter":
        cfg = EventConfig()
        kind = cfg.kind or "stdout"
        try:
            if kind == "stdout":
                return cls(StdoutSink())
            if kind == "webhook":
                if not cfg.webhook_url:
                    raise RuntimeError("EVENT_WEBHOOK_URL is required for webhook sink")
                return cls(WebhookSink(cfg.webhook_url, auth=cfg.webhook_auth))
            if kind == "redis":
                if not cfg.redis_dsn:
                    raise RuntimeError("EVENT_REDIS_DSN is required for redis sink")
                return cls(RedisSink(cfg.redis_dsn, channel=cfg.redis_channel))
            if kind == "nats":
                return cls(NatsSink(cfg.nats_url, subject=cfg.nats_subject))
        except Exception as e:
            print(json.dumps({"event": "event.sink.init.error", "sink": kind, "error": str(e)}), file=sys.stderr)
            # Fallback to stdout
            return cls(StdoutSink())
        # Unknown kind -> stdout
        return cls(StdoutSink())

    def emit(self, payload: Dict[str, Any]) -> None:
        try:
            self.sink.emit(payload)
        except Exception as e:
            print(json.dumps({"event": "event.emit.error", "error": str(e)}), file=sys.stderr)
