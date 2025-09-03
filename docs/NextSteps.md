## What we shipped

* **Core ingestor** (library + FastAPI + CLI)

  * deterministic normalize/segment/chunk; idempotent by `content_sha1`
  * profiles: `default/dense/sparse/markdown/screenplay/pdf_pages`
  * parsers for `.txt/.md/.pdf/.docx` (page sentinel + optional DOCX header/footer strip)

* **Watcher** (concurrency + backpressure)

  * workers, bounded queue, retries/backoff, subdirs, temp/partial ignores
  * success/fail moves with `.err.json` + `document.ingested/failed` events

* **Observability**

  * `/readyz` write-check, structured access logs w/ `X-Request-ID`
  * Prometheus metrics (http/ingest/resegment/search) + optional Pushgateway (best-effort)

* **FTS search**

  * FTS5 over `chunk.text`, `bm25` score + snippets
  * `GET /v1/search?q=&work_id=&limit=&rebuild=`

* **Temporal**

  * async activities (`asyncio.to_thread`) + `IngestWorkflow`/`PostIngestWorkflow`
  * resilient worker + “wait for Temporal” helper; Compose network stable (`lore-ingestor-temporal:7233`)
  * HTTP kicks PostIngestWorkflow (opt-in)

* **MCP server**

  * stdio server wrapping your HTTP API as MCP tools (Claude Desktop-ready)
  * tolerant tool errors when API is down

* **Docs & ops**

  * full `README.md` (quickstart, endpoints, CLI, watcher, Temporal, MCP)
  * upgraded Makefile (search, resegment, ready, metrics, logs, temporal-smoke)

---

## Tiny runbook (ops)

* **Healthy**: `/v1/readyz == {"ready":true}` and `/metrics` exports counters/histograms.
* **Ingest fails**: check HTTP logs for `document.failed`, watcher `.err.json`, Pushgateway `fail` increments.
* **Temporal**: worker logs include `worker up → target=…`. If not, check DNS to `lore-ingestor-temporal`, then UI.
* **FTS drift**: call `/v1/search?...&rebuild=true` once (or run migration script).
* **Pushgateway unreachable**: ingests still succeed (best-effort push).

---

## Sensible next milestones (pick & choose)

1. **Auth + multi-tenant**

   * simple API keys / JWT; per-tenant DBs or row-level scoping

2. **Review UI + highlights**

   * small FE to render scenes/chunks + FTS hits; POST accept/reject findings (ties into your Trope Miner)

3. **Event sink adapters**

   * Webhook retry queue; NATS subjects per event type; Redis stream option

4. **Temporal expansions**

   * `IngestWorkflow` end-to-end; signals for cancel/resume; per-work “maintenance” workflow (reseg, refresh FTS, etc.)

5. **Packaging**

   * publish `lore-ingest` to an internal index; pin requirements; prebuilt Docker images

6. **MCP extras**

   * tools for `works_summary/ids`, `kick_post_ingest(work_id)`, and maybe a “search→slice” helper

---
