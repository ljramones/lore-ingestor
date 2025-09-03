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

---
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