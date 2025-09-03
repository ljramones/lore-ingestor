# lore_ingest/pushgw.py
from __future__ import annotations

import os
from typing import Optional, Dict

from prometheus_client import CollectorRegistry, Counter, Gauge, push_to_gateway, pushadd_to_gateway

PGW_URL      = os.getenv("PUSHGATEWAY_URL", "").strip()
PGW_JOB      = os.getenv("PUSHGATEWAY_JOB", "lore_ingest")
PGW_INSTANCE = os.getenv("PUSHGATEWAY_INSTANCE", "")
PGW_MODE     = os.getenv("PUSHGATEWAY_MODE", "pushadd").lower()
PGW_TIMEOUT  = float(os.getenv("PUSHGATEWAY_TIMEOUT", "2.0"))  # seconds

def _grouping(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    g: Dict[str, str] = {}
    if PGW_INSTANCE:
        g["instance"] = PGW_INSTANCE
    if extra:
        g.update({k: str(v) for k, v in extra.items()})
    return g

def _maybe_url() -> Optional[str]:
    return PGW_URL if PGW_URL else None

def _safe_push(url: str, job: str, reg: CollectorRegistry, grouping: Dict[str, str]):
    """Best-effort push; swallow all errors."""
    try:
        if PGW_MODE == "push":
            # timeout supported via handler/timeout kw in newer prometheus_client; pass timeout
            push_to_gateway(url, job=job, registry=reg, grouping_key=grouping, timeout=PGW_TIMEOUT)
        else:
            pushadd_to_gateway(url, job=job, registry=reg, grouping_key=grouping, timeout=PGW_TIMEOUT)
    except Exception:
        # Never break the caller
        pass

def push_ingest(event_outcome: str, duration_s: Optional[float] = None, extra_labels: Optional[Dict[str, str]] = None):
    url = _maybe_url()
    if not url:
        return
    reg = CollectorRegistry()
    c = Counter("lore_ingest_events_total", "Total ingest/resegment events", ["event", "outcome"], registry=reg)
    g = Gauge("lore_ingest_event_last_duration_seconds", "Last event duration (seconds)", ["event"], registry=reg)
    c.labels("ingest", event_outcome).inc()
    if duration_s is not None:
        g.labels("ingest").set(duration_s)
    _safe_push(url, PGW_JOB, reg, _grouping(extra_labels))

def push_resegment(event_outcome: str, duration_s: Optional[float] = None, extra_labels: Optional[Dict[str, str]] = None):
    url = _maybe_url()
    if not url:
        return
    reg = CollectorRegistry()
    c = Counter("lore_ingest_events_total", "Total ingest/resegment events", ["event", "outcome"], registry=reg)
    g = Gauge("lore_ingest_event_last_duration_seconds", "Last event duration (seconds)", ["event"], registry=reg)
    c.labels("resegment", event_outcome).inc()
    if duration_s is not None:
        g.labels("resegment").set(duration_s)
    _safe_push(url, PGW_JOB, reg, _grouping(extra_labels))
