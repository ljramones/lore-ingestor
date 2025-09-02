# service/temporal_worker.py
from __future__ import annotations

import asyncio
import os
import sys

# Our activity
from lore_ingest.temporal import ingest_activity

# Optional dependency: temporalio
try:
    from temporalio.client import Client
    from temporalio.worker import Worker
except Exception:
    print(
        "temporalio is required to run the worker.\n"
        "Install with: pip install temporalio",
        file=sys.stderr,
    )
    raise SystemExit(2)


async def main() -> None:
    target = os.getenv("TEMPORAL_TARGET", "localhost:7233")
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    task_queue = os.getenv("INGEST_TASK_QUEUE", "ingest-queue")

    client = await Client.connect(target, namespace=namespace)

    worker = Worker(
        client=client,
        task_queue=task_queue,
        workflows=[],                 # add workflows later if you have them
        activities=[ingest_activity], # register the activity
    )

    print(f"[temporal] worker up → target={target} ns={namespace} queue={task_queue}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
