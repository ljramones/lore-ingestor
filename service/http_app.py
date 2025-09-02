# service/http_app.py
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional, List, Dict

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from lore_ingest.api import ingest_file, IngestResult
from lore_ingest.persist import open_db, ensure_ingest_columns_and_tables
from lore_ingest.segment_profiles import PROFILES
from lore_ingest.parsers import available_parsers

DB_PATH = os.getenv("DB_PATH", "/app/data/tropes.db")

app = FastAPI(title="Lore Ingest API", version="1.0")


def _init_db():
    conn = open_db(DB_PATH)
    ensure_ingest_columns_and_tables(conn)
    conn.close()


@app.get("/v1/healthz")
def healthz():
    return {"ok": True, "db": DB_PATH}


@app.get("/v1/readyz")
def readyz():
    try:
        _init_db()
        return {"ready": True}
    except Exception as e:
        return JSONResponse({"ready": False, "error": str(e)}, status_code=503)


@app.get("/v1/parsers")
def list_parsers() -> Dict[str, List[str]]:
    return {"parsers": available_parsers()}


@app.get("/v1/profiles")
def list_profiles() -> Dict[str, List[str]]:
    return {"profiles": sorted(PROFILES.keys())}


@app.get("/v1/works")
def list_works(q: Optional[str] = Query(None), limit: int = Query(50, ge=1, le=500)):
    conn = open_db(DB_PATH)
    ensure_ingest_columns_and_tables(conn)
    if q:
        rows = conn.execute(
            "SELECT id, title, author, char_count FROM work WHERE title LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"%{q}%", limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, title, author, char_count FROM work ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [{"id": r["id"], "title": r["title"], "author": r["author"], "chars": r["char_count"]} for r in rows]


@app.get("/v1/works/{work_id}")
def get_work(work_id: str):
    conn = open_db(DB_PATH)
    row = conn.execute(
        "SELECT id, title, author, char_count FROM work WHERE id = ?",
        (work_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="work not found")
    return {"id": row["id"], "title": row["title"], "author": row["author"], "chars": row["char_count"]}


@app.get("/v1/works/{work_id}/scenes")
def get_scenes(work_id: str):
    conn = open_db(DB_PATH)
    rows = conn.execute(
        "SELECT id, idx, char_start, char_end, heading FROM scene WHERE work_id = ? ORDER BY idx ASC",
        (work_id,),
    ).fetchall()
    return [{"scene_id": r["id"], "idx": r["idx"], "start": r["char_start"], "end": r["char_end"], "heading": r["heading"]} for r in rows]


@app.get("/v1/works/{work_id}/chunks")
def get_chunks(work_id: str):
    conn = open_db(DB_PATH)
    rows = conn.execute(
        "SELECT id, scene_id, idx, char_start, char_end FROM chunk WHERE work_id = ? ORDER BY idx ASC",
        (work_id,),
    ).fetchall()
    return [{"chunk_id": r["id"], "scene_id": r["scene_id"], "idx": r["idx"], "start": r["char_start"], "end": r["char_end"]} for r in rows]


@app.get("/v1/works/{work_id}/slice")
def get_slice(work_id: str, start: int, end: int):
    conn = open_db(DB_PATH)
    row = conn.execute("SELECT norm_text FROM work WHERE id = ?", (work_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="work not found")
    text = row["norm_text"] or ""
    start = max(0, min(start, len(text)))
    end = max(start, min(end, len(text)))
    return {"text": text[start:end]}


# ---------------- Ingest (JSON or multipart) ----------------

@app.post("/v1/ingest", status_code=201)
async def ingest(request: Request):
    """
    Accept either:
      - JSON: {"path": "...", "title"?, "author"?, "profile"?}
      - multipart/form-data: file=@..., (title|author|profile optional)
    """
    _init_db()
    ct = (request.headers.get("content-type") or "").lower()

    def _ok(res: IngestResult) -> JSONResponse:
        return JSONResponse(
            {"work_id": res.work_id, "content_sha1": res.content_sha1, "sizes": res.sizes},
            status_code=201,
        )

    # JSON mode
    if ct.startswith("application/json"):
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        if not isinstance(payload, dict) or "path" not in payload:
            raise HTTPException(status_code=400, detail="JSON requires 'path'")
        path = str(payload["path"])
        title = payload.get("title")
        author = payload.get("author")
        profile = payload.get("profile")
        try:
            res = ingest_file(path=path, title=title, author=author, db_path=DB_PATH, profile=profile)
            return _ok(res)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Ingest failed: {e}") from e

    # Multipart mode (file upload OR form path)
    if "multipart/form-data" in ct:
        try:
            form = await request.form()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid multipart form data")

        upload = form.get("file")
        title = form.get("title")
        author = form.get("author")
        profile = form.get("profile")

        if upload is not None and hasattr(upload, "filename"):
            suffix = Path(upload.filename or "upload").suffix
            tmp_path: Optional[str] = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(await upload.read())
                    tmp_path = tmp.name
                res = ingest_file(path=tmp_path, title=title, author=author, db_path=DB_PATH, profile=profile)
                return _ok(res)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Ingest failed: {e}") from e
            finally:
                if tmp_path:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

        # Fallback: multipart form with 'path' instead of file
        path = form.get("path")
        if not path:
            raise HTTPException(status_code=400, detail="Provide file=@... or form field 'path'")
        try:
            res = ingest_file(path=str(path), title=title, author=author, db_path=DB_PATH, profile=profile)
            return _ok(res)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Ingest failed: {e}") from e

    # Last attempt: tolerate missing/incorrect Content-Type if body is JSON
    try:
        payload = await request.json()
        if isinstance(payload, dict) and "path" in payload:
            res = ingest_file(
                path=str(payload["path"]),
                title=payload.get("title"),
                author=payload.get("author"),
                db_path=DB_PATH,
                profile=payload.get("profile"),
            )
            return _ok(res)
    except Exception:
        pass

    raise HTTPException(status_code=415, detail="Unsupported Content-Type. Use application/json or multipart/form-data.")
