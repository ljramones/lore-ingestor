# Lore-Ingestor

> A small, reliable ingestion service for long-form text files with profiles, a concurrent folder watcher, HTTP/CLI surfaces, events, full-text search, and optional Temporal automation. Bonus: an MCP server so you can use it from MCP-aware clients.

---

## TL;DR

* **What it does**: turns files (`.txt`, `.md`, `.pdf`, `.docx`) into a canonical SQLite DB (`work/scene/chunk`) with **deterministic segmentation**, emits **events**, exposes a **FastAPI** for reads/slices/search, and can **watch a folder** to process new files.
* **Profiles**: `default`, `dense`, `sparse`, `markdown` (heading split, fenced-code aware), `screenplay` (sluglines + character cues), `pdf_pages` (strict per-page).
* **Ops**: structured access logs w/ request IDs, `/readyz` that **writes** to DB, `/metrics` (Prometheus), optional **Pushgateway**, event sinks (**stdout/HTTP/Redis/NATS**).
* **Automation** (optional): **Temporal** worker hosting ingest/summary activities and a tiny **PostIngestWorkflow**; the HTTP service can kick a workflow after each ingest.
* **MCP** (optional): a small Python MCP server that wraps the HTTP API as MCP tools for Claude Desktop or any MCP client.

---

## Goals

* Extract ingestion into a **small module/service** with a stable interface (library + CLI + HTTP).
* Decouple analyzers from file I/O. **DB is the contract** (`work/scene/chunk`).
* Production-friendly: **idempotent** by `content_sha1`, **observable**, and **event-driven**.
* Easy to run: **Docker Compose** with sensible defaults; local dev via `pip install -e .`.

---

## Architecture (short)

* `lore_ingest/` — library (normalize / segment / chunk / persist / parsers / profiles / events / temporal stub)
* `service/http_app.py` — FastAPI app (`/v1/*` + `/metrics`)
* `service/watcher.py` — concurrent folder watcher (workers, backpressure, retries, subdir support)
* `service/temporal_worker.py` — Temporal worker (activities + workflows)
* `service/wait_for_temporal.py` — tiny helper that waits for Temporal before exec’ing the worker
* `cli/main.py` — CLI: `ingest`, `watch`, `works`, `works-ls`, `resegment`
* `mcp_server/server.py` — MCP server (stdio) exposing HTTP API as MCP tools
* SQLite schema — `work`, `scene`, `chunk`, `ingest_run` (+ `chunk_fts` for search)
* Events — `document.ingested` and `document.failed` via sinks (stdout/http/redis/nats)

---

## Quickstart (Docker Compose)

### Prereqs

* Docker Desktop (macOS/Windows) or Docker Engine (Linux)
* Ports: HTTP `8099`, Temporal UI `8233`, Redis `6379`, NATS `4222`, Pushgateway `9091`

> Compose uses a single bridge network `ingest-network`. Temporal server is named `lore-ingestor-temporal`; worker and UI connect to `lore-ingestor-temporal:7233`.

### Start the stack

```bash
docker compose up -d --build
docker compose ps
```

Services:

* `http`: FastAPI on `http://localhost:8099`
* `watcher`: concurrent folder watcher
* `temporal` + `temporal-ui`: Temporal server/UI (`http://localhost:8233`)
* `temporal-worker`: activities/workflows (`ingest-queue`)
* `redis`, `nats`, `pushgateway` (optional sinks)

### Health & ready

```bash
curl -s http://127.0.0.1:8099/v1/healthz | jq
curl -s http://127.0.0.1:8099/v1/readyz  | jq
```

### Ingest a file (multipart)

```bash
echo -e "CHAPTER I\nHello\n\n\nWorld" > inbox/sample.txt
curl -s -F "file=@inbox/sample.txt" -F "title=Sample" http://127.0.0.1:8099/v1/ingest | jq
```

On success you’ll see a `work_id` in the response, an `ingested` event in the HTTP logs, and (if enabled) a **PostIngestWorkflow** in the Temporal UI.

> Temporal: The worker uses `service/wait_for_temporal.py` to wait for `lore-ingestor-temporal:7233` before starting, then retries inside `service/temporal_worker.py` if needed.

---

## HTTP API (selected)

* `GET /v1/healthz` — quick check
* `GET /v1/readyz` — **writes** to DB to verify readiness
* `GET /v1/parsers` — supported extensions
* `GET /v1/profiles` — available segmentation profiles
* `GET /v1/works?limit=&offset=&q=&author=` — list works (search over title/author)
* `GET /v1/works/{id}` — summary (`id,title,author,source,content_sha1,chars`)
* `GET /v1/works/{id}/scenes` — scene offsets
* `GET /v1/works/{id}/chunks` — chunk offsets
* `GET /v1/works/{id}/slice?start=&end=` — exact substring
* `POST /v1/ingest` — JSON (`{"path": ...,"title":...,"profile":...}`) or multipart (`file=@...`)
* `POST /v1/works/{id}/resegment` — rebuild scenes/chunks; body: `{"profile":"pdf_pages","window_chars":512,"stride_chars":384}`
* `GET /v1/search?q=&work_id=&limit=&offset=&rebuild=` — FTS search over `chunk.text` (bm25 + snippet)
* `GET /metrics` — Prometheus metrics (http, ingest, resegment, search)

**Example — search:**

```bash
curl -s 'http://127.0.0.1:8099/v1/search?q=alpha&limit=10' | jq
# restrict to a work
curl -s 'http://127.0.0.1:8099/v1/search?q="battle NEAR forest"&work_id=<uuid>' | jq
```

---

## CLI

Install editable for local dev:

```bash
pip install -r requirements.txt
pip install -e .
lore-ingest --help
```

Common commands:

```bash
# one-shot ingest
lore-ingest ingest inbox/file.txt --title "My Title" --profile markdown --db data/tropes.db

# resegment an existing work with a different profile
lore-ingest resegment --work-id <uuid> --profile pdf_pages --db data/tropes.db

# list works (IDs only) and rich listing
lore-ingest works --db data/tropes.db --ids-only
lore-ingest works-ls --db data/tropes.db

# run the watcher from CLI (local dev)
lore-ingest watch
```

---

## Watcher (concurrency & backpressure)

The watcher scans `INBOX`, enqueues stable files, and ingests with a pool of worker threads.

Key env (already set in compose):

* `WATCH_WORKERS` (default 2) — parallel ingest workers
* `WATCH_MAX_QUEUE` (default 100) — queue capacity
* `WATCH_STABLE_MS` (default 750) — size must be stable before enqueue
* `WATCH_POLL_SECONDS` (default 1.0) — scan interval
* `WATCH_RETRIES` (default 2), `WATCH_BACKOFF_BASE_MS` (default 250) — retry/backoff
* `WATCH_RECURSIVE` (`true|false`) — subdir support
* `ALLOWED_EXT` (default `.txt,.md,.pdf,.docx`)
* `MAX_FILE_MB` (default `20`)
* `INGEST_PROFILE` (e.g., `pdf_pages`)

**Behavior**:

* Ignored: dotfiles, `*.tmp`, `*.crdownload`, `*.partial`, `~$*`, `.~lock*`
* Oversized/unsupported → `fail/<ts>__file.ext` + `.err.json` + `document.failed` + Pushgateway `fail`
* Success → `success/<work_id>__file.ext` + `document.ingested` + Pushgateway `ok`

---

## Profiles & parsers

* **Profiles**:
  `default`, `dense`, `sparse`,
  `markdown` (split on `#` headings; **fenced code** ignored),
  `screenplay` (sluglines `INT./EXT./EST./INT/EXT` + **character cues** & transitions),
  `pdf_pages` (**strict per-page**; sentinel inserted by PDF parser).

* **Parsers**:

  * `.txt`, `.md` — detect encoding; normalize
  * `.pdf` — `pypdf` extracts pages; we insert `[[PAGE_BREAK]]`
  * `.docx` — `docx2txt`; optional header/footer strip via `DOCX_STRIP_HF=true`

---

## Events & sinks

On every ingest:

* `document.ingested`
  `{type, work_id, path, title, author, content_sha1, sizes{chars,scenes,chunks}, profile, created_at, [run_id]}`

On failure:

* `document.failed`
  `{type, path, reason, stage, profile, created_at}`

**Sinks** (configure via env in compose):

* `EMIT_SINK=stdout,redis,nats,http` (any combo)
* `EMIT_HTTP_URL`, `EMIT_REDIS_URL`, `EMIT_REDIS_LIST`, `EMIT_NATS_URL`, `EMIT_NATS_SUBJECT`

---

## Observability

* **Structured access logs** with `X-Request-ID`
* `/v1/readyz` actually **writes** to verify DB writeability
* **Prometheus metrics** at `/metrics`:

  * HTTP totals/latency
  * Ingest totals/latency
  * Resegment totals/latency
  * Search totals/latency
* **Pushgateway (optional)**:

  * Set `PUSHGATEWAY_URL=http://pushgateway:9091`
  * Pushes job-style `ok|fail` counters/gauges for ingest/resegment in **HTTP**, **watcher**, **CLI**
  * Safe (best-effort), won’t break requests if unreachable

---

## Temporal (optional)

* **Worker** (`temporal-worker`) hosts:

  * `IngestActivity` (uses `ingest_file`, offloaded via `asyncio.to_thread`)
  * `GetSummaryActivity` (DB summary, offloaded via `asyncio.to_thread`)
  * `IngestWorkflow` (ingest + summary)
  * `PostIngestWorkflow` (summary only; kicked after `/v1/ingest` when enabled)
* **Enable kick** from HTTP:

  ```
  TEMPORAL_ENABLED=true
  TEMPORAL_TARGET=lore-ingestor-temporal:7233
  TEMPORAL_NAMESPACE=default
  TEMPORAL_TASK_QUEUE=ingest-queue
  ```
* **UI**: [http://localhost:8233](http://localhost:8233)

**Smoke**:

* Ingest via HTTP; a `PostIngestWorkflow` run should appear in the UI.

---

## MCP server (optional)

A tiny MCP server exposes your HTTP API as MCP **tools** for Claude Desktop or any MCP client.

* Install deps:

  ```
  pip install mcp httpx
  ```
* Run manually (stdio; will just wait):

  ```
  LORE_INGEST_URL=http://127.0.0.1:8099 python -m mcp_server.server
  ```
* Claude Desktop config (macOS):
  `~/Library/Application Support/Claude/claude_desktop_config.json`

  ```json
  {
    "mcpServers": {
      "lore-ingestor": {
        "command": "/opt/anaconda3/bin/python",
        "args": ["-m", "mcp_server.server"],
        "cwd": "/Users/larrymitchell/ML/writer-agents/Lore-Ingestor",
        "env": { "LORE_INGEST_URL": "http://127.0.0.1:8099" }
      }
    }
  }
  ```

  Restart Claude Desktop, then call tools like `healthz`, `works_list`, `ingest(path=...)`, `search`, `resegment`.

> If the API is down, tools will return a clean error (they won’t crash the server).

---

## Local development

```bash
# create venv and install local package + deps
pip install -r requirements.txt
pip install -e .

# run the API locally
uvicorn service.http_app:app --reload --port 8099

# run tests
pytest -q

# Temporal unit (time-skipping env)
pytest -q tests/test_temporal_workflow.py
```

---

## Troubleshooting

**`/v1/ingest` returns 500 with `<urlopen error ...>`**
Pushgateway is unreachable. Either remove `PUSHGATEWAY_URL` or keep the wrapped `pushgw.py` (best-effort) so pushes never break requests.

**Multipart upload returns 400**
Ensure `python-multipart` is installed (in `requirements.txt`) and the API is running.

**Ingest returns 201 but PDF has 1 scene**
Use `profile=pdf_pages` (strict per page). Markdown fences & screenplay cues are separate profiles.

**Temporal worker can’t connect**
Use `TEMPORAL_TARGET=lore-ingestor-temporal:7233`. Ensure both services are on `ingest-network`. The worker uses `service/wait_for_temporal.py` to wait for the server and also retries forever in `service/temporal_worker.py`.

**Docker “parent snapshot … does not exist” at build/export**
Reset BuildKit cache:

```
docker compose down -v
docker buildx prune --all --force
docker builder prune --all --force
# optional: restart Docker Desktop
docker compose build --no-cache --pull http temporal-worker watcher
```

**FTS search returns 500**
FTS is lazily ensured; pass `rebuild=true` the first time, or apply the migration in `sql/migrations/002_chunk_fts.sql`.

---

## License

MIT (or your preferred license). Contributions welcome.

---

### Appendix: Environment variables (HTTP/watcher highlights)

| Service     | Env var                              | Default                       | Purpose                                |
| ----------- | ------------------------------------ | ----------------------------- | -------------------------------------- |
| http        | `DB_PATH`                            | `/app/data/tropes.db`         | SQLite DB path                         |
| http        | `TEMPORAL_ENABLED`                   | `false`                       | Kick workflow after ingest             |
| http/worker | `TEMPORAL_TARGET`                    | `lore-ingestor-temporal:7233` | Temporal host\:port                    |
| http        | `EMIT_SINK`                          | `stdout`                      | Event sinks (`stdout,redis,nats,http`) |
| http        | `PUSHGATEWAY_URL`                    | —                             | Pushgateway endpoint (optional)        |
| watcher     | `INBOX` / `SUCCESS_DIR` / `FAIL_DIR` | `/app/inbox`…                 | Watcher paths                          |
| watcher     | `WATCH_WORKERS`                      | `2`                           | Parallel ingest workers                |
| watcher     | `WATCH_MAX_QUEUE`                    | `100`                         | Queue capacity                         |
| watcher     | `WATCH_STABLE_MS`                    | `750`                         | Debounce window                        |
| watcher     | `WATCH_RETRIES`                      | `2`                           | Retry attempts                         |
| watcher     | `WATCH_BACKOFF_BASE_MS`              | `250`                         | Retry backoff base                     |
| all         | `INGEST_PROFILE`                     | `default`                     | Default profile for ingest             |

---
