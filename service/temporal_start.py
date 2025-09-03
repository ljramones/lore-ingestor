# service/temporal_start.py
from __future__ import annotations

import asyncio
import os
import time
from typing import Optional

from temporalio.client import Client


async def _start_post_ingest(work_id: str, *, content_sha1: Optional[str], profile: Optional[str]) -> None:
    target = os.getenv("TEMPORAL_TARGET", "localhost:7233")
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    task_queue = os.getenv("TEMPORAL_TASK_QUEUE", "ingest-queue")

    client = await Client.connect(target, namespace=namespace)

    # Make workflow IDs human-friendly but unique across runs
    wf_id = f"post-ingest-{work_id}-{int(time.time())}"
    await client.start_workflow(
        "PostIngestWorkflow",  # workflow name registered by the worker
        work_id=work_id,
        content_sha1=content_sha1,
        profile=profile,
        id=wf_id,
        task_queue=task_queue,
    )


def maybe_start_post_ingest(work_id: str, *, content_sha1: Optional[str], profile: Optional[str]) -> None:
    """
    Fire-and-forget starter; only runs if TEMPORAL_ENABLED=true.
    Safe to call from your HTTP handler after a successful ingest.
    """
    if os.getenv("TEMPORAL_ENABLED", "false").lower() not in {"1", "true", "yes"}:
        return
    # schedule an async task; do not await to avoid blocking request
    try:
        asyncio.get_running_loop().create_task(_start_post_ingest(work_id, content_sha1=content_sha1, profile=profile))
    except RuntimeError:
        # no running loop (unlikely in FastAPI); run in a one-off loop
        asyncio.run(_start_post_ingest(work_id, content_sha1=content_sha1, profile=profile))
