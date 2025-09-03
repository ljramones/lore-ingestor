# service/temporal_worker.py
from __future__ import annotations

import asyncio
import os
import re
import sys
import time

from temporalio.client import Client
from temporalio.worker import Worker

from lore_ingest.temporal import (
    ingest_activity,
    get_summary_activity,
    IngestWorkflow,
    PostIngestWorkflow,
)

_SCHEME_RE = re.compile(r"^\s*(?P<scheme>[a-z]+)://", re.I)

def _normalize_target(raw: str | None) -> str:
    # Accept host:port or (mistakenly) scheme://host:port and normalize to host:port
    if not raw:
        return "temporal:7233"
    raw = raw.strip()
    m = _SCHEME_RE.match(raw)
    if m:
        raw = raw[m.end():]  # strip scheme://
    # strip any trailing slashes/whitespace
    return raw.strip("/")

async def _connect_with_retry(addr: str, namespace: str, attempts: int = 10) -> Client:
    delay = 0.5
    for i in range(attempts):
        try:
            return await Client.connect(addr, namespace=namespace)
        except Exception as e:
            if i == attempts - 1:
                print(f"[temporal] connect failed target={addr} ns={namespace} err={e}", file=sys.stderr)
                raise
            print(f"[temporal] connect retry {i+1}/{attempts} target={addr} ns={namespace} err={e}")
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 5.0)

async def main() -> None:
    raw_target = os.getenv("TEMPORAL_TARGET", "temporal:7233")
    target = _normalize_target(raw_target)
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    task_queue = os.getenv("TEMPORAL_TASK_QUEUE", "ingest-queue")

    print(f"[temporal] worker starting → target={target} ns={namespace} queue={task_queue}")
    client = await _connect_with_retry(target, namespace)

    worker = Worker(
        client=client,
        task_queue=task_queue,
        workflows=[IngestWorkflow, PostIngestWorkflow],
        activities=[ingest_activity, get_summary_activity],
    )
    print(f"[temporal] worker up → target={target} ns={namespace} queue={task_queue}")
    await worker.run()

if __name__ == "__main__":
    asyncio.run(main())
