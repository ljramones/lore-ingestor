# docs/INGESTION\_PLAN.md

> **Status:** Draft (2025‑09‑01) · **Owner:** Larry M. · **Scope:** Extract ingestion into standalone lib/CLI/HTTP + folder watcher; keep Trope Miner unchanged.

---

## 0) TL;DR

Split the pipeline into **(A) a standalone ingestion service** that turns files into a canonical DB representation (`work/scene/chunk`) and **(B) analyzers** (e.g., Trope Miner) that only read from that DB. A **folder watcher** ingests new files from `inbox/`, moves to `success/` or `fail/`, and emits `document.ingested` events. The ingestion lib is **Temporal‑friendly** and idempotent via `content_sha1`. MCP comes later as a thin adapter.

---

## 1) Goals & Non‑Goals (this phase)

### Goals

* Extract a clean **domain library** `lore_ingest` (text normalize → segment → persist) with a tiny, stable interface (lib + CLI + HTTP).
* **Folder watcher**: watch a drop folder, ingest new files, move on success/failure; emit events.
* **DB is the contract**: analyzers consume `work/scene/chunk` only; no file I/O coupling.
* **Run stamping & events**: record `ingest_run` and emit `document.ingested` events.
* **Temporal‑friendly**: operations idempotent (keyed by `content_sha1`) and callable synchronously.

### Non‑Goals

* No Trope Miner refactor (it already works against the DB).
* No MCP server yet; add later once HTTP/lib is stable.

---

## 2) Architecture Overview (ports & adapters)

```mermaid
flowchart LR
  subgraph Ingestion Service
    FW[Folder Watcher] -->|path| LIB[Library: lore_ingest]
    CLI[(CLI: lore-ingest)] --> LIB
    HTTP[(HTTP: FastAPI)] --> LIB
  end

  LIB -->|persist| DB[(SQLite: tropes.db)]
  LIB -->|emit| EV[Event Bridge]
  EV --> OUT[[stdout sink]]
  DB --> TM[Trope Miner]
  DB --> UI[Review UI]
  Temporal[Temporal] -->|Activity: IngestActivity(path)| HTTP
```

### Components

* **Domain lib (`lore_ingest`)**

  * `ingest_file(path, title?, author?, rules?) -> work_id`
  * utilities: `compute_sha1`, `detect_encoding`, `normalize_text`, `segment_to_scenes`, `chunk_scene`, `persist(db)`
  * writes: `work`, `scene`, `chunk`, `ingest_run`, `work.content_sha1`
* **CLI**: `lore-ingest ingest <path> --title --author --db <sqlite>`
* **HTTP (FastAPI)**

  * `POST /v1/ingest {path|bytes, title?, author?} -> {work_id}`
  * `GET  /v1/works/:id`, `/scenes`, `/chunks`, `/slice?start=&end=`
  * `GET  /v1/healthz`
* **Folder watcher** (watchdog)

  * Watches `inbox/`; on new file calls `ingest_file(...)`
  * On success: move to `success/` and emit `document.ingested`
  * On failure: move to `fail/` and write `<name>.err.json`

---

## 3) Data Model (reader‑friendly, analyzer‑agnostic)

All offsets are **absolute** into `work.norm_text`.

```sql
-- canonical tables
CREATE TABLE IF NOT EXISTS work (
  id TEXT PRIMARY KEY,
  title TEXT,
  author TEXT,
  norm_text TEXT NOT NULL,
  content_sha1 TEXT NOT NULL,
  ingest_run_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scene (
  id TEXT PRIMARY KEY,
  work_id TEXT NOT NULL,
  idx INTEGER NOT NULL,             -- 0..N-1 within a work
  char_start INTEGER NOT NULL,
  char_end INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunk (
  id TEXT PRIMARY KEY,
  work_id TEXT NOT NULL,
  scene_id TEXT NOT NULL,
  idx INTEGER NOT NULL,             -- 0..M-1 within a work (or per scene)
  char_start INTEGER NOT NULL,
  char_end INTEGER NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_run (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  params_json TEXT NOT NULL
);

-- helpful indexes
CREATE INDEX IF NOT EXISTS idx_scene_work_idx  ON scene(work_id, idx);
CREATE INDEX IF NOT EXISTS idx_chunk_work_idx  ON chunk(work_id, idx);
CREATE INDEX IF NOT EXISTS idx_work_sha1       ON work(content_sha1);
```

**Notes**

* Scenes partition the work; chunks are retrieval windows (subset/overlap of scenes).
* Optional: add `scene_fts(text)` virtual table later if analyzers need keyword search.

---

## 4) Library API (Python)

```python
# lore_ingest/api.py
from typing import Optional, Dict

def ingest_file(path: str, *, title: Optional[str] = None,
                author: Optional[str] = None,
                rules: Optional[Dict] = None,
                db_path: str = "./tropes.db") -> str:
    """Ingests a file and returns work_id. Idempotent by content_sha1."""

# utilities guaranteed stable for analyzers/tests
compute_sha1(bytes_or_str) -> str
normalize_text(raw: str) -> str
segment_to_scenes(text: str, rules: Optional[Dict]) -> list[tuple[int,int]]  # [(start,end)]
chunk_scene(text: str, *, window_chars=512, stride_chars=384) -> list[tuple[int,int]]
persist(db_path, work, scenes, chunks, run_params) -> work_id
```

**Idempotency**: compute `content_sha1` over **normalized source text**. If a row exists in `work` with same digest, return its `work_id` without re‑writing scenes/chunks.

---

## 5) CLI

```bash
# basic
lore-ingest ingest ./inbox/my_novel.txt --title "The Girl" --author "Larry Mitchell" --db ./tropes.db

# options
--rules ./segmentation_rules.yaml      # optional rule profile
--window 512 --stride 384              # chunking knobs (if exposed)
--quiet / --verbose
--emit-json                            # print {work_id,...} to stdout
```

Exit codes: `0` success, `2` unsupported file type, `3` parse error, `4` DB unavailable, `5` other.

---

## 6) HTTP API (FastAPI v1)

### Health

`GET /v1/healthz` → `{ "ok": true }`

### Ingest

`POST /v1/ingest`

```json
{
  "path": "inbox/file.txt",
  "title": "The Girl",
  "author": "Larry Mitchell"
}
```

→ `201 Created`

```json
{ "work_id": "uuid", "content_sha1": "…", "sizes": {"chars":64210,"scenes":18,"chunks":180} }
```

Errors: `400` invalid input, `415` unsupported type, `500` parse error.

> Alternative: `multipart/form-data` for raw bytes; when both `path` and bytes are provided, bytes win.

### Read

* `GET /v1/works/{id}` → `{id,title,author,chars,content_sha1,created_at}`
* `GET /v1/works/{id}/scenes` → `[{scene_id,idx,start,end}]`
* `GET /v1/works/{id}/chunks` → `[{chunk_id,scene_id,idx,start,end}]`
* `GET /v1/works/{id}/slice?start=&end=` → `{ "text": "…exact substring…" }`

**Error semantics**: `404` if `work_id`/`scene_id` unknown; `416` if slice out of range.

---

## 7) Folder Watcher (service)

**Inputs**: `.txt`, `.md`, `.docx`, `.pdf` (extensible). Others → fail with reason.

**Workflow**

1. On file create → debounce 500ms (avoid partial copies).
2. Try parse → normalize → segment → chunk → persist.
3. On success:

   * Move to `success/<work_id>__<original-filename>`
   * Emit `document.ingested` to stdout
4. On failure:

   * Move to `fail/<timestamp>__<original-filename>`
   * Write `fail/<...>.err.json` with `{message, stack, stage}`

**Idempotency**: If `content_sha1` already exists → treat as success and move to `success/` (no duplicate rows).

---

## 8) Events & Schemas

**Event type**: `document.ingested`

Payload (example):

```json
{
  "type": "document.ingested",
  "work_id": "uuid",
  "path": "inbox/file.txt",
  "title": "The Girl",
  "author": "Larry Mitchell",
  "content_sha1": "…",
  "sizes": {"chars": 64210, "scenes": 18, "chunks": 180},
  "run_id": "uuid",
  "created_at": "2025-09-01T19:45:22Z"
}
```

**JSON Schema (draft/indicative)**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["type","work_id","content_sha1","sizes","created_at"],
  "properties": {
    "type": {"const": "document.ingested"},
    "work_id": {"type": "string"},
    "path": {"type": "string"},
    "title": {"type": "string"},
    "author": {"type": "string"},
    "content_sha1": {"type": "string"},
    "sizes": {
      "type": "object",
      "properties": {
        "chars": {"type": "integer"},
        "scenes": {"type": "integer"},
        "chunks": {"type": "integer"}
      },
      "required": ["chars","scenes","chunks"]
    },
    "run_id": {"type": "string"},
    "created_at": {"type": "string", "format": "date-time"}
  }
}
```

---

## 9) Temporal Hooks

* **Activity** `IngestActivity(path) -> work_id` calls **library** (preferred) or **HTTP**.
* **Retries** safe due to idempotency on `content_sha1`.
* **Signals**: downstream workflow `TropeMinerWorkflow(work_id)` can be triggered on `document.ingested`.

---

## 10) Config & Ops

**Environment**

```env
DB_PATH=./tropes.db
INBOX=./inbox
SUCCESS_DIR=./success
FAIL_DIR=./fail
ALLOWED_EXT=.txt,.md,.docx,.pdf
MAX_FILE_MB=20
CHUNK_WINDOW=512
CHUNK_STRIDE=384
EVENT_SINK=stdout   # future: nats://..., redis://..., http://webhook
```

**Logging**

* Structured JSON to stdout; include `event`, `work_id`, `run_id`, `sha1`, `duration_ms`.

**Metrics (optional)**

* `ingest_duration_ms` (histogram), `ingest_errors_total` (counter by stage), `ingest_bytes_total` (counter), `ingest_queue_depth` (gauge).

**Health**

* `/v1/healthz` responds `200` if DB reachable + watcher thread alive.

---

## 11) Parsing Policies

* **Encoding detection**: chardet/utf‑8 default; hard fail if undecodable.
* **Normalization**: CRLF→LF, collapse Windows smart quotes, strip trailing spaces, ensure final newline.
* **Scene policy**: keep current heuristic; allow rules profile via YAML (pluggable later).
* **PDF/DOCX**: start with `pypdf`/`docx2txt`; surface extraction warnings in `ingest_run.params_json`.

---

## 12) Acceptance Criteria

* [ ] `lore_ingest` library extracts text, normalizes, segments, persists (existing schema).
* [ ] CLI: `lore-ingest ingest …` prints `{work_id}` and populates DB deterministically.
* [ ] HTTP service: `/healthz` and `/v1/ingest` working locally.
* [ ] Folder watcher: moves files to `success/` or `fail/` and emits `document.ingested` to stdout.
* [ ] Idempotency: dropping the same file twice does **not** duplicate a work (based on `content_sha1`).
* [ ] Trope Miner & Review UI continue to function reading the same DB.
* [ ] Tests: fixture ingest → scene count & offsets match; slice API returns exact substrings.

---

## 13) Test Plan (initial)

**Unit**

* `compute_sha1` stable across platforms/encodings.
* `normalize_text` preserves semantic content; snapshot tests.
* `segment_to_scenes` produces deterministic spans for fixtures.
* `chunk_scene` produces expected windows/stride.

**Integration**

* Ingest `.txt`, `.md`, `.pdf`, `.docx` fixtures → verify `work/scene/chunk` rows and counts.
* `GET /v1/works/{id}/slice?start=&end=` round‑trips exact substrings.
* Idempotent re‑ingest of the same file returns original `work_id` and does not duplicate rows.

**Watcher**

* Dropping a supported file → moved to `success/` and event emitted.
* Dropping an unsupported file → moved to `fail/` with `.err.json`.

**Performance (smoke)**

* 10 MB `.txt` ingests < N seconds on M4 Max; memory bounded; no DB locks > 100ms.

---

## 14) Work Plan (tickets)

1. **Extract lib**: move logic from `ingestor_segmenter.py` → `lore_ingest/` with clean functions.
2. **CLI wrapper**: Typer/argparse entrypoint (`lore-ingest ingest`).
3. **HTTP service**: FastAPI with pydantic models + shared lib wiring.
4. **Folder watcher**: watchdog worker + move/emit logic + `.env` config.
5. **Run stamping**: create `ingest_run` row each ingest; persist parser/rules in `params_json`.
6. **Event bridge**: `document.ingested` via existing bridge; stdout sink default.
7. **Smoke tests**: fixtures + checks; Makefile targets; docs.

---

## 15) Open Choices

* **Scene policy**: keep current heuristic vs. pluggable rules.
* **FTS**: add `scene_fts(text)` virtual table for analyzers (optional).
* **Versioning**: overwrite `work` by digest vs. `work_version` table later.
* **Event transport**: stdout now; NATS/Redis/HTTP webhook later.
* **Chunk strategy**: per‑scene vs. global rolling window (current: per‑scene).

---

## 16) Directory Layout (proposed)

```
repo/
├─ docs/
│  └─ INGESTION_PLAN.md
├─ lore_ingest/
│  ├─ __init__.py
│  ├─ api.py
│  ├─ normalize.py
│  ├─ segment.py
│  ├─ chunk.py
│  ├─ persist.py
│  └─ parsers/ (txt, md, pdf, docx)
├─ service/
│  ├─ http_app.py        # FastAPI
│  └─ watcher.py         # watchdog loop
├─ cli/
│  └─ main.py            # lore-ingest
├─ tests/
│  ├─ fixtures/
│  ├─ test_api.py
│  ├─ test_segment.py
│  ├─ test_http.py
│  └─ test_watcher.py
└─ Makefile
```

---

## 17) Makefile (starter targets)

```Makefile
.PHONY: venv dev run-http watch ingest test fmt lint
venv:
	uv venv || python3 -m venv .venv

deV:
	. .venv/bin/activate && pip install -U pip && pip install -r requirements.txt

run-http:
	. .venv/bin/activate && uvicorn service.http_app:app --reload --port 8099

watch:
	. .venv/bin/activate && python -m service.watcher

ingest:
	. .venv/bin/activate && python -m cli.main ingest $(FILE) --db ./tropes.db

test:
	. .venv/bin/activate && pytest -q

fmt:
	ruff format .

lint:
	ruff check .
```

---

# README.md pointer (drop‑in section)

Add this short section near the top of your **top‑level README.md** to orient contributors:

````markdown
## Ingestion Service (Standalone)

We’ve extracted file ingestion into a standalone library/CLI/HTTP service so analyzers (e.g., Trope Miner) read from a stable DB contract (`work/scene/chunk`). A folder watcher now ingests files dropped into `inbox/`, moves them to `success/` or `fail/`, and emits `document.ingested` events. The service is Temporal‑ready and idempotent via `content_sha1`.

- **Plan & API**: see [docs/INGESTION_PLAN.md](docs/INGESTION_PLAN.md)
- **Quickstart**:
  ```bash
  # start HTTP
  make run-http
  # watch a folder
  make watch
  # one‑shot ingest
  make ingest FILE=./inbox/my.txt
````

````



---

## 18) Schema elements extracted from `sql/ingestion.sql`

> Snapshot of what exists today, grouped by concern, plus a brief "delta vs plan" so we know what to add/keep.

### Pragmas (SQLite)
- `PRAGMA foreign_keys = ON;`
- `PRAGMA journal_mode = WAL;`
- `PRAGMA synchronous = NORMAL;`

### A) Core text model (ingestion contract)
**Tables**
- **work** `(id TEXT PK, title TEXT, author TEXT, source TEXT, license TEXT, raw_text BLOB, norm_text TEXT, char_count INTEGER, created_at TEXT DEFAULT now)`
  - Indexes: `idx_work_title(title)`, `idx_work_author(author)`
- **chapter** `(id TEXT PK, work_id TEXT FK→work ON DELETE CASCADE, idx INT, title TEXT, char_start INT, char_end INT)`
  - Index: `idx_chapter_work_idx(work_id, idx)`
- **scene** `(id TEXT PK, work_id TEXT FK→work CASCADE, chapter_id TEXT FK→chapter SET NULL, idx INT, char_start INT, char_end INT, heading TEXT)`
  - Index: `idx_scene_work_idx(work_id, idx)`
- **chunk** `(id TEXT PK, work_id TEXT FK→work CASCADE, scene_id TEXT FK→scene SET NULL, idx INT, char_start INT, char_end INT, token_start INT, token_end INT, text TEXT NOT NULL, sha256 TEXT NOT NULL)`
  - Indexes: `idx_chunk_work_sha(work_id, sha256)`, `idx_chunk_work_idx(work_id, idx)`, `idx_chunk_work_scene(work_id, scene_id, idx)`, `idx_chunk_scene(scene_id)`, `idx_chunk_work_span(work_id, char_start, char_end)`
- **embedding_ref** `(chunk_id TEXT FK→chunk CASCADE, collection TEXT, model TEXT, dim INT, chroma_id TEXT, PRIMARY KEY(chunk_id, collection))`
  - Indexes: `idx_embedding_model(model)`, `idx_embedding_collection(collection)`
- **chunk_fts** — virtual table: `fts5(text, content='chunk', content_rowid='rowid')`

**Triggers (for FTS mirror)**
- `chunk_fts_after_insert` (insert row into FTS after `chunk` insert)
- `chunk_fts_after_delete` (delete from FTS after `chunk` delete)
- `chunk_fts_after_update_text` (replace FTS row after `chunk.text` update)

> **Contract‑critical fields** for analyzers: `scene(idx, char_start, char_end)`, `chunk(idx, char_start, char_end, text)`. Offsets are absolute into `work.norm_text`.

### B) Trope catalog & mining (analyzer domain)
**Catalog**
- **trope** `(id PK, name UNIQUE, summary, long_desc, tags JSON, source_url, aliases JSON, anti_aliases JSON, created_at, updated_at)`
  - Index: `idx_trope_name(name)`
- **trope_alias** `(trope_id FK→trope, alias TEXT, priority INT DEFAULT 100, is_blocked INT DEFAULT 0, PRIMARY KEY (trope_id, alias))`
- **trope_relation** `(src_id FK→trope, dst_id FK→trope, rel ENUM, PRIMARY KEY(src_id, dst_id, rel))`
  - Indexes: `idx_tr_src(src_id, rel)`, `idx_tr_dst(dst_id, rel)`
- **trope_example** `(id PK, trope_id FK→trope, quote, work_title, work_author, location, url)`
  - Index: `idx_te_trope(trope_id)`

**Mining artifacts**
- **trope_candidate** `(id PK, work_id FK, scene_id FK, chunk_id FK, trope_id FK, surface, alias, start INT, end INT, source TEXT DEFAULT 'gazetteer', score REAL DEFAULT 0.0, created_at)`
  - Indexes: `idx_tc_work_scene(work_id, scene_id)`, `idx_tc_chunk(chunk_id)`, `idx_tc_trope(trope_id)`
- **trope_finding** `(id PK, work_id FK, scene_id FK, chunk_id FK, trope_id FK, level ENUM('span','scene','work'), confidence REAL NOT NULL, rationale TEXT, evidence_start INT, evidence_end INT, created_at, model, verifier_score REAL, verifier_flag TEXT, calibration_version TEXT, threshold_used REAL)`
  - Indexes: `idx_tf_work_scene(work_id, scene_id)`, `idx_tf_trope(trope_id)`, `idx_finding_work(work_id)`, `idx_tf_trope_created(trope_id, created_at)`, `idx_tf_verifier_flag(verifier_flag)`, `idx_tf_calib(calibration_version)`

### C) Trope families / groups (optional taxonomy)
- **trope_group** `(id PK, name UNIQUE)`
- **trope_group_member** `(trope_id FK, group_id FK, PRIMARY KEY(trope_id, group_id))`
  - Indexes: `idx_tgm_group(group_id)`, `idx_tgm_trope(trope_id)`
- **v_trope_group** view: `trope_id → group_name`

### D) Rerank support + sanity (priors used by judge)
- **support_selection** `(scene_id, chunk_id, rank, stage1_score, stage2_score, picked DEFAULT 1, created_at DEFAULT now, PRIMARY KEY(scene_id, chunk_id))`
  - Indexes: `idx_support_scene(scene_id)`, `idx_support_scene_rank(scene_id, rank)`
- **trope_sanity** `(scene_id, trope_id, lex_ok INT, sem_sim REAL, weight REAL, created_at DEFAULT now, PRIMARY KEY(scene_id, trope_id))`

### E) Human review
- **trope_finding_human** `(id PK, finding_id FK→trope_finding, decision ENUM('accept','reject','edit'), corrected_start INT, corrected_end INT, corrected_trope_id FK→trope, note, reviewer, created_at DEFAULT now)`
  - Index: `idx_tfh_finding(finding_id)`
- **v_latest_human** view: latest human decision per finding

### F) Helpful views
- **v_recent_findings**: `trope_finding` × `trope` (ordered by `created_at DESC`)
- **v_scene_counts**: per‑scene counts of findings / accepted / rejected (joins `v_latest_human`)

### G) Cleanup & uniqueness
- One‑time cleanup: delete duplicate rows in `trope_candidate` and `trope_finding` by `(work_id, trope_id, span)`
- Uniqueness constraints (indexes):
  - `uniq_candidate_span` on `trope_candidate(work_id, trope_id, start, end)`
  - `uq_finding_span` on `trope_finding(work_id, trope_id, evidence_start, evidence_end)`

---

### What belongs to which layer
- **Ingestion‑only (must exist for analyzers/UI):** `work`, `chapter`, `scene`, `chunk`, **FTS** (`chunk_fts` + triggers), `embedding_ref` (if using Chroma).
- **Analyzer domain (Trope Miner):** `trope`, `trope_alias`, `trope_relation`, `trope_example`, `trope_candidate`, `trope_finding`, `support_selection`, `trope_sanity`, taxonomy tables (`trope_group*`).
- **Review app:** `trope_finding_human`, `v_latest_human`, `v_scene_counts`, `v_recent_findings`.

### Delta vs. target plan (gaps to add)
- **Missing from SQL but in plan:**
  - `work.content_sha1` (for idempotent ingest by digest)
  - `ingest_run(id, created_at, params_json)` + `work.ingest_run_id`
  - HTTP‑/CLI‑visible **slice** contract is implicit; schema OK
- **Present in SQL but not called out in plan:**
  - `chapter` table (keep; optional in minimal ingest)
  - `chunk.token_start/token_end` (nice‑to‑have if tokenization stage exists)
  - `chunk.sha256` (per‑chunk hash; keep alongside `work.content_sha1`)

### Quick ER sketch
```mermaid
erDiagram
  work ||--o{ chapter : has
  work ||--o{ scene : has
  chapter ||--o{ scene : contains
  work ||--o{ chunk : has
  scene ||--o{ chunk : contains
  chunk ||--o| embedding_ref : has

  trope ||--o{ trope_alias : has
  trope ||--o{ trope_example : has
  trope ||--o{ trope_relation : relates

  work ||--o{ trope_candidate : yields
  scene ||--o{ trope_candidate : yields
  chunk ||--o{ trope_candidate : yields
  trope ||--o{ trope_candidate : targets

  work ||--o{ trope_finding : yields
  scene ||--o{ trope_finding : yields
  chunk ||--o{ trope_finding : yields
  trope ||--o{ trope_finding : targets

  scene ||--o{ support_selection : picks
  scene ||--o{ trope_sanity : rates

  trope_finding ||--o{ trope_finding_human : reviewed_by
````

> If you want, I can generate a migration patch to add `work.content_sha1` and `ingest_run` while preserving existing data.
