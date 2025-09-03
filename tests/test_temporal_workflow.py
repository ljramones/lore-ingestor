# tests/test_temporal_workflow.py
from __future__ import annotations

import os
from pathlib import Path

import pytest

from temporalio.testing import WorkflowEnvironment
from temporalio.client import Client
from temporalio.worker import Worker

# Import your activities/workflows
from lore_ingest.temporal import (
    IngestWorkflow,
    ingest_activity,
    get_summary_activity,
)

@pytest.mark.asyncio
async def test_ingest_workflow_time_skipping(tmp_path: Path):
    """
    End-to-end Temporal test:
      - starts a local time-skipping test environment
      - spins a Worker with your activities/workflows
      - executes IngestWorkflow on a temp file/DB
      - validates summary counts
    """
    # Prepare a small source file + temp DB
    src = tmp_path / "doc.txt"
    src.write_text("CHAPTER I\nHello\n\n\nWorld\n", encoding="utf-8")
    db = tmp_path / "temporal.db"

    # Start ephemeral Temporal test env (no external services needed)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        # Connect a client to the test environment
        client = await Client.connect(env.target_host)

        task_queue = "test-ingest-queue"

        # Run a Worker hosting the workflow + activities
        async with Worker(
            client,
            task_queue=task_queue,
            workflows=[IngestWorkflow],
            activities=[ingest_activity, get_summary_activity],
        ):
            # Execute the workflow (pass db_path/profile just like HTTP)
            result = await client.execute_workflow(
                IngestWorkflow.run,
                id=f"ingest-{os.getpid()}",
                task_queue=task_queue,
                input={
                    "path": src.as_posix(),
                    "title": "Doc",
                    "author": None,
                    "db_path": db.as_posix(),
                    "profile": "markdown",
                },
            )

    # Validate shape
    assert "ingest" in result and "summary" in result
    assert isinstance(result["ingest"]["work_id"], str) and result["ingest"]["work_id"]
    assert result["summary"]["chars"] >= 5
    assert result["summary"]["scenes"] >= 1
    assert result["summary"]["chunks"] >= 1
