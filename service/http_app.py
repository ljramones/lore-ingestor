# service/http_app.py
from __future__ import annotations

import os
import time
import uuid
import json
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError

from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

from lore_ingest.api import ingest_file, resegment_work, IngestResult
from lore_ingest.persist import open_db, ensure_ingest_columns_and_tables
from lore_ingest.segment_profiles import PROFILES
from lore_ingest.parsers import available_parsers
from lore_ingest.events import build_ingested_event, build_failed_event, emit_async
from lore_ingest.pushgw import push_ingest, push_resegment

from service.temporal_start import maybe_start_post_ingest


# -------------------- App & Env --------------------

APP_NAME = "Lore Ingest API"
DB_PATH = os.getenv("DB_PATH", "/app/data/tropes.db")
INBOX = os.getenv("INBOX", "/app/inbox")

# CORS env toggles (open by default)
CORS_ENABLED = os.getenv("CORS_ENABLED", "true").lower() not in {"0", "false", "no"}
CORS_ALLOW_ORIGINS = os.getenv("CORS_ALLOW_ORIGINS", "*")
CORS_ALLOW_METHODS = os.getenv("CORS_ALLOW_METHODS", "*")
CORS_ALLOW_HEADERS = os.getenv("CORS_ALLOW_HEADERS", "*")

app = FastAPI(title=APP_NAME, version="1.5")

if CORS_ENABLED:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in CORS_ALLOW_ORIGINS.split(",")] if CORS_ALLOW_ORIGINS != "*" else ["*"],
        allow_methods=[m.strip() for m in CORS_ALLOW_METHODS.split(",")] if CORS_ALLOW_METHODS != "*" else ["*"],
        allow_headers=[h.strip() for h in CORS_ALLOW_HEADERS.split(",")] if CORS_ALLOW_HEADERS != "*" else ["*"],
    )

# -------------------- Prometheus metrics --------------------

HTTP_REQ_COUNT = Counter(
    "http_requests_total",
    "Count of HTTP requests",
    ["method", "route", "status"],
)

HTTP_REQ_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency (seconds)",
    ["method", "route", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)

INGEST_TOTAL = Counter(
    "ingest_total",
    "Total ingests by outcome",
    ["outcome"],  # ok|fail
)

INGEST_LATENCY = Histogram(
    "ingest_duration_seconds",
    "Ingest duration (seconds)",
    ["outcome"],  # ok|fail
)

RESEGMENT_TOTAL = Counter(
    "resegment_total",
    "Total resegment operations by outcome",
    ["outcome"],  # ok|fail
)

RESEGMENT_LATENCY = Histogram(
    "resegment_duration_seconds",
    "Resegment duration (seconds)",
    ["outcome"],  # ok|fail
)

SEARCH_TOTAL = Counter(
    "fts_search_total",
    "Total FTS searches by outcome",
    ["outcome"],  # ok|fail
)

SEARCH_LATENCY = Histogram(
    "fts_search_duration_seconds",
    "FTS search latency (seconds)",
    ["outcome"],  # ok|fail
)

# -------------------- Error shapes --------------------

@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "error": {"type": "HTTPError", "message": exc.detail}},
    )

@app.exception_handler(RequestValidationError)
async def validation_exc_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"ok": False, "error": {"type": "ValidationError", "message": "Invalid request", "details": exc.errors()}},
    )

@app.exception_handler(Exception)
async def unhandled_exc_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error": {"type": "ServerError", "message": str(exc)}},
    )

# -------------------- Request ID + Access log middleware --------------------

@app.middleware("http")
async def request_id_and_metrics(request: Request, call_next):
    # Request ID in/out
    req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    start = time.perf_counter()
    route = request.url.path  # keep raw path; can group later by router name
    method = request.method

    try:
        response = await call_next(request)
        status_str = str(response.status_code)
        dur = time.perf_counter() - start
        # Set header so clients can correlate
        response.headers["X-Request-ID"] = req_id

        # Structured access log (real JSON)
        print(json.dumps({
            "event": "access",
            "req_id": req_id,
            "method": method,
            "path": route,
            "status": int(status_str),
            "duration_ms": int(dur * 1000),
        }, ensure_ascii=False))

        # Metrics
        HTTP_REQ_COUNT.labels(method=method, route=route, status=status_str).inc()
        HTTP_REQ_LATENCY.labels(method=method, route=route, status=status_str).observe(dur)

        return response
    except Exception as e:
        status_str = "500"
        dur = time.perf_counter() - start
        print(json.dumps({
            "event": "access",
            "req_id": req_id,
            "method": method,
            "path": route,
            "status": 500,
            "duration_ms": int(dur * 1000),
            "err": str(e),
        }, ensure_ascii=False))
        HTTP_REQ_COUNT.labels(method=method, route=route, status=status_str).inc()
        HTTP_REQ_LATENCY.labels(method=method, route=route, status=status_str).observe(dur)
        raise

# -------------------- Helpers --------------------

def _init_db():
    conn = open_db(DB_PATH)
    ensure_ingest_columns_and_tables(conn)
    conn.close()

def _ensure_chunk_fts(conn, rebuild: bool = False) -> None:
    """
    Create chunk_fts + triggers if missing; optional rebuild.
    """
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chunk_fts'")
    if not cur.fetchone():
        conn.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
              text,
              content='chunk',
              content_rowid='rowid'
            );
            CREATE TRIGGER IF NOT EXISTS chunk_fts_after_insert AFTER INSERT ON chunk
            BEGIN
              INSERT INTO chunk_fts(rowid, text) VALUES (new.rowid, new.text);
            END;
            CREATE TRIGGER IF NOT EXISTS chunk_fts_after_delete AFTER DELETE ON chunk
            BEGIN
              INSERT INTO chunk_fts(chunk_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
            END;
            CREATE TRIGGER IF NOT EXISTS chunk_fts_after_update_text AFTER UPDATE OF text ON chunk
            BEGIN
              INSERT INTO chunk_fts(chunk_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
              INSERT INTO chunk_fts(rowid, text) VALUES (new.rowid, new.text);
            END;
            """
        )
        rebuild = True
    if rebuild:
        conn.execute("INSERT INTO chunk_fts(chunk_fts) VALUES ('rebuild')")
        conn.commit()

# -------------------- Health / Ready --------------------

@app.get("/v1/healthz")
def healthz():
    return {"ok": True, "db": DB_PATH}

@app.get("/v1/readyz")
def readyz():
    """
    Check read + write:
      - Ensure schema
      - BEGIN IMMEDIATE; create __readyz; insert; delete; COMMIT
    """
    try:
        conn = open_db(DB_PATH)
        ensure_ingest_columns_and_tables(conn)
        conn.execute("BEGIN IMMEDIATE;")
        conn.execute("CREATE TABLE IF NOT EXISTS __readyz (ts TEXT NOT NULL);")
        conn.execute("INSERT INTO __readyz (ts) VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'));")
        conn.execute("DELETE FROM __readyz;")
        conn.commit()
        conn.close()
        return {"ready": True}
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return JSONResponse({"ready": False, "error": str(e)}, status_code=503)

# -------------------- Discovery --------------------

@app.get("/v1/parsers")
def list_parsers() -> Dict[str, List[str]]:
    aps = available_parsers()
    if isinstance(aps, dict):
        return {"parsers": sorted(list(aps.keys()))}
    return {"parsers": list(aps)}

@app.get("/v1/profiles")
def list_profiles() -> Dict[str, List[str]]:
    return {"profiles": sorted(PROFILES.keys())}

# -------------------- Works: search / list / get / slice --------------------

@app.get("/v1/works")
def list_works(
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    q: Optional[str] = Query(None, description="Substring match on title OR author"),
    author: Optional[str] = Query(None, description="Substring match on author only"),
):
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        sql = (
            "SELECT id, title, author, COALESCE(char_count,0) AS chars, created_at "
            "FROM work "
        )
        args: List[Any] = []
        where: List[str] = []
        if q:
            like = f"%{q}%"
            where.append("(COALESCE(title,'') LIKE ? OR COALESCE(author,'') LIKE ?)")
            args.extend([like, like])
        if author:
            where.append("COALESCE(author,'') LIKE ?")
            args.append(f"%{author}%")
        if where:
            sql += "WHERE " + " AND ".join(where) + " "
        sql += "ORDER BY datetime(created_at) DESC LIMIT ? OFFSET ?"
        args.extend([limit, offset])

        rows = conn.execute(sql, args).fetchall()
        return [
            {"id": r["id"], "title": r["title"], "author": r["author"], "chars": r["chars"], "created_at": r["created_at"]}
            for r in rows
        ]
    finally:
        conn.close()

@app.get("/v1/works/{work_id}")
def get_work(work_id: str):
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, title, author, source, content_sha1, COALESCE(char_count,0) AS chars FROM work WHERE id = ?",
            (work_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="work not found")
        return {
            "id": row["id"],
            "title": row["title"],
            "author": row["author"],
            "source": row["source"],
            "content_sha1": row["content_sha1"],
            "chars": row["chars"],
        }
    finally:
        conn.close()

@app.get("/v1/works/{work_id}/scenes")
def get_scenes(work_id: str):
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, idx, char_start, char_end, heading FROM scene WHERE work_id = ? ORDER BY idx ASC",
            (work_id,),
        ).fetchall()
        return [{"scene_id": r["id"], "idx": r["idx"], "start": r["char_start"], "end": r["char_end"], "heading": r["heading"]} for r in rows]
    finally:
        conn.close()

@app.get("/v1/works/{work_id}/chunks")
def get_chunks(work_id: str):
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, scene_id, idx, char_start, char_end FROM chunk WHERE work_id = ? ORDER BY idx ASC",
            (work_id,),
        ).fetchall()
        return [{"chunk_id": r["id"], "scene_id": r["scene_id"], "idx": r["idx"], "start": r["char_start"], "end": r["char_end"]} for r in rows]
    finally:
        conn.close()

@app.get("/v1/works/{work_id}/slice")
def get_slice(work_id: str, start: int = Query(..., ge=0), end: int = Query(..., gt=0)):
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT norm_text, COALESCE(char_count,0) AS chars FROM work WHERE id = ?", (work_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="work not found")
        text = row["norm_text"] or ""
        n = int(row["chars"] or 0)
        if end <= start or start < 0 or end > n:
            raise HTTPException(status_code=416, detail="slice out of range")
        return {"text": text[start:end]}
    finally:
        conn.close()

@app.get("/v1/works/ids")
def list_work_ids(
    q: Optional[str] = Query(None, description="Substring match on title/author"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if q:
            like = f"%{q}%"
            rows = conn.execute(
                "SELECT id FROM work WHERE COALESCE(title,'') LIKE ? OR COALESCE(author,'') LIKE ? "
                "ORDER BY datetime(created_at) DESC LIMIT ? OFFSET ?",
                (like, like, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM work ORDER BY datetime(created_at) DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return {"ids": [r["id"] for r in rows]}
    finally:
        conn.close()

@app.get("/v1/works/summary")
def list_work_summary(
    q: Optional[str] = Query(None, description="Substring match on title/author"),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """
    Returns: [{id, title, author, chars, scenes, chunks, created_at}]
    """
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        base = (
            "SELECT w.id, w.title, w.author, COALESCE(w.char_count,0) AS chars, "
            "       w.created_at, COALESCE(sc.scenes,0) AS scenes, COALESCE(ch.chunks,0) AS chunks "
            "FROM work w "
            "LEFT JOIN (SELECT work_id, COUNT(*) AS scenes FROM scene GROUP BY work_id) sc ON sc.work_id = w.id "
            "LEFT JOIN (SELECT work_id, COUNT(*) AS chunks FROM chunk GROUP BY work_id) ch ON ch.work_id = w.id "
        )
        args: List[Any] = []
        where: List[str] = []
        if q:
            like = f"%{q}%"
            where.append("(COALESCE(w.title,'') LIKE ? OR COALESCE(w.author,'') LIKE ?)")
            args.extend([like, like])
        if where:
            base += "WHERE " + " AND ".join(where) + " "
        base += "ORDER BY datetime(w.created_at) DESC LIMIT ? OFFSET ?"
        args.extend([limit, offset])

        rows = conn.execute(base, args).fetchall()
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "author": r["author"],
                "chars": r["chars"],
                "scenes": r["scenes"],
                "chunks": r["chunks"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()

# -------------------- FTS search --------------------
@app.get("/v1/search")
def search(
    q: str = Query(..., description="FTS5 query string"),
    work_id: Optional[str] = Query(None, description="restrict to a single work"),
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
    rebuild: bool = Query(False, description="force FTS rebuild before searching"),
):
    import sqlite3
    t0 = time.perf_counter()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        ensure_ingest_columns_and_tables(conn)
        _ensure_chunk_fts(conn, rebuild=rebuild)

        sql = (
            "SELECT c.id AS chunk_id, c.scene_id, c.idx, c.char_start AS start, c.char_end AS end, "
            "       bm25(chunk_fts) AS score, "
            "       snippet(chunk_fts, -1, '[', ']', ' … ', 8) AS snippet "
            "FROM chunk_fts JOIN chunk c ON c.rowid = chunk_fts.rowid "
            "WHERE chunk_fts MATCH ? "
        )
        args: List[Any] = [q]
        if work_id:
            sql += "AND c.work_id = ? "
            args.append(work_id)
        sql += "ORDER BY score LIMIT ? OFFSET ?"
        args.extend([limit, offset])

        rows = conn.execute(sql, args).fetchall()
        hits = [
            {
                "chunk_id": r["chunk_id"],
                "scene_id": r["scene_id"],
                "idx": r["idx"],
                "start": r["start"],
                "end": r["end"],
                "score": float(r["score"]) if r["score"] is not None else None,
                "snippet": r["snippet"],
            }
            for r in rows
        ]
        SEARCH_TOTAL.labels(outcome="ok").inc()
        SEARCH_LATENCY.labels(outcome="ok").observe(time.perf_counter() - t0)
        return {"q": q, "work_id": work_id, "count": len(hits), "hits": hits}
    except Exception as e:
        SEARCH_TOTAL.labels(outcome="fail").inc()
        SEARCH_LATENCY.labels(outcome="fail").observe(time.perf_counter() - t0)
        raise HTTPException(status_code=500, detail=f"FTS search failed: {e}") from e
    finally:
        conn.close()

# -------------------- Metrics endpoint --------------------
@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# -------------------- Ingest (JSON + multipart) --------------------
@app.post("/v1/ingest", status_code=201)
async def ingest(request: Request):
    _init_db()
    ct = (request.headers.get("content-type") or "").lower()
    t0 = time.perf_counter()

    def _ok(res: IngestResult, src: str, title: Optional[str], author: Optional[str], profile: Optional[str]) -> JSONResponse:
        # Build + emit event (success)
        ev = build_ingested_event(
            db_path=DB_PATH,
            work_id=res.work_id,
            source_path=src,
            title=title,
            author=author,
            content_sha1=res.content_sha1,
            sizes=res.sizes,
            profile=profile,
        )
        emit_async(ev)

        # Metrics + Pushgateway
        INGEST_TOTAL.labels(outcome="ok").inc()
        INGEST_LATENCY.labels(outcome="ok").observe(time.perf_counter() - t0)
        push_ingest("ok", duration_s=time.perf_counter() - t0, extra_labels={"source": "http"})

        # Temporal: kick a post-ingest workflow if enabled (non-blocking)
        maybe_start_post_ingest(res.work_id, content_sha1=res.content_sha1, profile=profile)

        return JSONResponse(
            {"work_id": res.work_id, "content_sha1": res.content_sha1, "sizes": res.sizes},
            status_code=201,
        )

    # ---------- JSON mode ----------
    if ct.startswith("application/json"):
        try:
            payload = await request.json()
        except Exception:
            INGEST_TOTAL.labels(outcome="fail").inc()
            INGEST_LATENCY.labels(outcome="fail").observe(time.perf_counter() - t0)
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        if not isinstance(payload, dict) or "path" not in payload:
            INGEST_TOTAL.labels(outcome="fail").inc()
            INGEST_LATENCY.labels(outcome="fail").observe(time.perf_counter() - t0)
            raise HTTPException(status_code=400, detail="JSON requires 'path'")

        path   = str(payload["path"])
        title  = payload.get("title")
        author = payload.get("author")
        profile = payload.get("profile")

        try:
            res = ingest_file(path=path, title=title, author=author, db_path=DB_PATH, profile=profile)
            return _ok(res, src=path, title=title, author=author, profile=profile)
        except Exception as e:
            # Failed event + metrics + push
            fev = build_failed_event(
                source_path=path, title=title, author=author, reason=str(e), stage="ingest-json", profile=profile
            )
            emit_async(fev)
            INGEST_TOTAL.labels(outcome="fail").inc()
            INGEST_LATENCY.labels(outcome="fail").observe(time.perf_counter() - t0)
            push_ingest("fail", duration_s=time.perf_counter() - t0, extra_labels={"source": "http"})
            raise HTTPException(status_code=500, detail=f"Ingest failed: {e}") from e

    # ---------- Multipart mode (file or form 'path') ----------
    if "multipart/form-data" in ct:
        form = await request.form()
        upload  = form.get("file")
        title   = form.get("title")
        author  = form.get("author")
        profile = form.get("profile")

        if upload is not None and hasattr(upload, "filename"):
            suffix = Path(upload.filename or "upload").suffix
            tmp_path: Optional[str] = None
            try:
                Path(INBOX).mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=INBOX) as tmp:
                    tmp.write(await upload.read())
                    tmp_path = tmp.name

                res = ingest_file(path=tmp_path, title=title, author=author, db_path=DB_PATH, profile=profile)
                # success handled in one place
                return _ok(res, src=f"multipart:{upload.filename}", title=title, author=author, profile=profile)

            except Exception as e:
                fev = build_failed_event(
                    source_path=f"multipart:{getattr(upload,'filename',None)}",
                    title=title, author=author, reason=str(e), stage="ingest-multipart", profile=profile
                )
                emit_async(fev)
                INGEST_TOTAL.labels(outcome="fail").inc()
                INGEST_LATENCY.labels(outcome="fail").observe(time.perf_counter() - t0)
                push_ingest("fail", duration_s=time.perf_counter() - t0, extra_labels={"source": "http"})
                raise HTTPException(status_code=500, detail=f"Ingest failed: {e}") from e
            finally:
                if tmp_path:
                    try:
                        Path(tmp_path).unlink(missing_ok=True)
                    except Exception:
                        pass

        # Multipart fallback: form 'path'
        path = form.get("path")
        if not path:
            INGEST_TOTAL.labels(outcome="fail").inc()
            INGEST_LATENCY.labels(outcome="fail").observe(time.perf_counter() - t0)
            raise HTTPException(status_code=400, detail="Provide file=@... or form field 'path'")

        try:
            res = ingest_file(path=str(path), title=title, author=author, db_path=DB_PATH, profile=profile)
            return _ok(res, src=str(path), title=title, author=author, profile=profile)
        except Exception as e:
            fev = build_failed_event(
                source_path=str(path), title=title, author=author, reason=str(e), stage="ingest-formpath", profile=profile
            )
            emit_async(fev)
            INGEST_TOTAL.labels(outcome="fail").inc()
            INGEST_LATENCY.labels(outcome="fail").observe(time.perf_counter() - t0)
            push_ingest("fail", duration_s=time.perf_counter() - t0, extra_labels={"source": "http"})
            raise HTTPException(status_code=500, detail=f"Ingest failed: {e}") from e

    # ---------- Last attempt: JSON body without header ----------
    try:
        payload = await request.json()
        if isinstance(payload, dict) and "path" in payload:
            title  = payload.get("title")
            author = payload.get("author")
            profile = payload.get("profile")
            res = ingest_file(path=str(payload["path"]), title=title, author=author, db_path=DB_PATH, profile=profile)
            return _ok(res, src=str(payload["path"]), title=title, author=author, profile=profile)
    except Exception:
        # JSON parse failure at this late stage -> count as fail (no reliable path/title)
        push_ingest("fail", duration_s=time.perf_counter() - t0, extra_labels={"source": "http"})
        pass

    # Unsupported
    INGEST_TOTAL.labels(outcome="fail").inc()
    INGEST_LATENCY.labels(outcome="fail").observe(time.perf_counter() - t0)
    raise HTTPException(status_code=415, detail="Unsupported Content-Type. Use application/json or multipart/form-data.")


# -------------------- Force resegment --------------------

@app.post("/v1/works/{work_id}/resegment")
def http_resegment(work_id: str, body: Dict[str, Any]):
    profile = body.get("profile")
    window = int(body.get("window_chars", 512))
    stride = int(body.get("stride_chars", 384))
    t0 = time.perf_counter()

    import sqlite3
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, title, author, source, content_sha1 FROM work WHERE id = ?",
        (work_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="work not found")

    try:
        res = resegment_work(work_id=work_id, db_path=DB_PATH, profile=profile, window_chars=window, stride_chars=stride)
        ev = build_ingested_event(
            db_path=DB_PATH, work_id=work_id, source_path=row["source"] or f"resegment:{work_id}",
            title=row["title"], author=row["author"], content_sha1=row["content_sha1"],
            sizes=res.sizes, profile=profile, extra={"resegment": True},
        )
        emit_async(ev)
        RESEGMENT_TOTAL.labels(outcome="ok").inc()
        RESEGMENT_LATENCY.labels(outcome="ok").observe(time.perf_counter() - t0)
        push_resegment("ok", duration_s=time.perf_counter() - t0, extra_labels={"source": "http"})

        return {"ok": True, "work_id": work_id, "sizes": res.sizes, "profile": profile or "default"}
    except Exception as e:
        fev = build_failed_event(
            source_path=row["source"] or f"resegment:{work_id}",
            title=row["title"], author=row["author"],
            reason=str(e), stage="resegment", profile=profile,
        )
        emit_async(fev)
        RESEGMENT_TOTAL.labels(outcome="fail").inc()
        RESEGMENT_LATENCY.labels(outcome="fail").observe(time.perf_counter() - t0)
        push_resegment("fail", duration_s=time.perf_counter() - t0, extra_labels={"source": "http"})
        raise HTTPException(status_code=500, detail=f"Resegment failed: {e}") from e

# -------------------- Metrics endpoint --------------------

@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
