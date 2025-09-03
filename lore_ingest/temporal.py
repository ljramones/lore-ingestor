# lore_ingest/temporal.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Dict, Any


import asyncio
from temporalio import activity, workflow

from lore_ingest.api import ingest_file
from lore_ingest.persist import open_db




# ---------------- Activities (dict inputs; NO keyword-only args) ----------------

@activity.defn(name="IngestActivity")
async def ingest_activity(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    params: {
      "path": str,
      "title": Optional[str],
      "author": Optional[str],
      "db_path": Optional[str],
      "profile": Optional[str]
    }
    """
    path = str(params.get("path"))
    title = params.get("title")
    author = params.get("author")
    db_path = params.get("db_path") or os.getenv("DB_PATH", "./tropes.db")
    profile = params.get("profile")

    # Run the blocking ingest_file in a thread
    res = await asyncio.to_thread(
        ingest_file,
        path=Path(path).as_posix(),
        title=title,
        author=author,
        db_path=db_path,
        profile=profile
    )
    return {"work_id": res.work_id, "content_sha1": res.content_sha1, "sizes": res.sizes}


@activity.defn(name="GetSummaryActivity")
async def get_summary_activity(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    params: {
      "work_id": str,
      "db_path": Optional[str]
    }
    """
    work_id = str(params.get("work_id"))
    db_path = params.get("db_path") or os.getenv("DB_PATH", "./tropes.db")

    def _get_summary():
        conn = open_db(db_path)
        try:
            row = conn.execute("SELECT COALESCE(char_count,0) AS chars FROM work WHERE id = ?", (work_id,)).fetchone()
            if not row:
                return {"work_id": work_id, "chars": 0, "scenes": 0, "chunks": 0}
            chars = int(row["chars"] or 0)
            scenes = conn.execute("SELECT COUNT(*) FROM scene WHERE work_id = ?", (work_id,)).fetchone()[0]
            chunks = conn.execute("SELECT COUNT(*) FROM chunk WHERE work_id = ?", (work_id,)).fetchone()[0]
            return {"work_id": work_id, "chars": chars, "scenes": scenes, "chunks": chunks}
        finally:
            conn.close()

    # Run the database operations in a thread
    return await asyncio.to_thread(_get_summary)


# ---------------- Workflows ----------------

@workflow.defn(name="IngestWorkflow")
class IngestWorkflow:
    """Demonstration workflow that ingests a path via activity, then summarizes."""

    @workflow.run
    async def run(
        self,
        *,
        path: str,
        title: Optional[str] = None,
        author: Optional[str] = None,
        db_path: str = "./tropes.db",
        profile: Optional[str] = None,
    ) -> Dict[str, Any]:
        # Call ingest as a dict param
        res = await workflow.execute_activity(
            ingest_activity,
            {"path": path, "title": title, "author": author, "db_path": db_path, "profile": profile},
            schedule_to_close_timeout=workflow.timedelta(seconds=120),
            start_to_close_timeout=workflow.timedelta(seconds=90),
            retry_policy=workflow.RetryPolicy(maximum_attempts=3),
        )

        summary = await workflow.execute_activity(
            get_summary_activity,
            {"work_id": res["work_id"], "db_path": db_path},
            schedule_to_close_timeout=workflow.timedelta(seconds=60),
        )
        return {"ingest": res, "summary": summary}


@workflow.defn(name="PostIngestWorkflow")
class PostIngestWorkflow:
    """Demo workflow to 'kick off after event'—just fetches a summary for the ingested work."""

    @workflow.run
    async def run(
        self,
        *,
        work_id: str,
        content_sha1: Optional[str] = None,
        profile: Optional[str] = None,
        db_path: str = "./tropes.db",
    ) -> Dict[str, Any]:
        summary = await workflow.execute_activity(
            get_summary_activity,
            {"work_id": work_id, "db_path": db_path},
            schedule_to_close_timeout=workflow.timedelta(seconds=60),
        )
        return {"work_id": work_id, "content_sha1": content_sha1, "profile": profile, "summary": summary}
