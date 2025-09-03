# =========================
# Lore Ingestor — Service & CLI (gmake)
# =========================
.RECIPEPREFIX := >

# ---- Config (override on the command line) -------------------------------
DB_PATH        ?= ./tropes.db
INBOX          ?= ./inbox
SUCCESS_DIR    ?= ./success
FAIL_DIR       ?= ./fail

HOST           ?= 127.0.0.1
PORT           ?= 8099

WINDOW         ?= 512
STRIDE         ?= 384
ALLOWED_EXT    ?= .txt,.md,.pdf,.docx
MAX_FILE_MB    ?= 20

SCHEMA         ?= ./sql/ingestion.sql
PY             ?= python3
UVICORN        ?= uvicorn

# Optional inputs for certain targets
FILE      ?=
TITLE     ?=
AUTHOR    ?=
WORK_ID   ?=
START     ?=
END       ?=
PROFILE   ?=
Q         ?=
AUTHOR_Q  ?=
LIMIT     ?= 25
OFFSET    ?= 0
REBUILD   ?= 0

# Temporal (local dev runner)
TEMPORAL_TARGET    ?= localhost:7233
TEMPORAL_NAMESPACE ?= default
INGEST_TASK_QUEUE  ?= ingest-queue

# ---- Sanitize config (strip stray whitespace) ------------------------------
DB_PATH     := $(strip $(DB_PATH))
INBOX       := $(strip $(INBOX))
SUCCESS_DIR := $(strip $(SUCCESS_DIR))
FAIL_DIR    := $(strip $(FAIL_DIR))
HOST        := $(strip $(HOST))
PORT        := $(strip $(PORT))
WINDOW      := $(strip $(WINDOW))
STRIDE      := $(strip $(STRIDE))
ALLOWED_EXT := $(strip $(ALLOWED_EXT))
MAX_FILE_MB := $(strip $(MAX_FILE_MB))
SCHEMA      := $(strip $(SCHEMA))
FILE        := $(strip $(FILE))
TITLE       := $(strip $(TITLE))
AUTHOR      := $(strip $(AUTHOR))
WORK_ID     := $(strip $(WORK_ID))
START       := $(strip $(START))
END         := $(strip $(END))
PROFILE     := $(strip $(PROFILE))
Q           := $(strip $(Q))
AUTHOR_Q    := $(strip $(AUTHOR_Q))
LIMIT       := $(strip $(LIMIT))
OFFSET      := $(strip $(OFFSET))
REBUILD     := $(strip $(REBUILD))
TEMPORAL_TARGET    := $(strip $(TEMPORAL_TARGET))
TEMPORAL_NAMESPACE := $(strip $(TEMPORAL_NAMESPACE))
INGEST_TASK_QUEUE  := $(strip $(INGEST_TASK_QUEUE))

# ---- Helpers --------------------------------------------------------------
SQLITE3 := sqlite3 "$(DB_PATH)"
CURL    := curl -fsS

# ---- Phony ----------------------------------------------------------------
.PHONY: help whereis dirs dev venv deps \
        run-http open health ready metrics \
        watch ingest ingest-api \
        dbinit migrate dbcheck works-ls scenes-ls chunks-ls slice-api slice-sql \
        works-ids works-summary search resegment-api \
        test fmt lint \
        docker-build docker-up docker-down docker-up-core \
        logs-http logs-worker logs-temporal \
        temporal-env worker temporal-smoke \
        clean clean-dirs

help:
> echo "Targets:"
> echo "  whereis         Show config (DB/paths/HTTP/Temporal) and availability hints"
> echo "  dirs            Create $(INBOX) $(SUCCESS_DIR) $(FAIL_DIR)"
> echo "  dev             Install dev deps (pytest, ruff)"
> echo "  run-http        Start FastAPI on $(HOST):$(PORT)"
> echo "  open            Open Swagger UI"
> echo "  health          GET /v1/healthz"
> echo "  ready           GET /v1/readyz (DB write check)"
> echo "  metrics         GET /metrics (Prometheus text)"
> echo "  watch           Start folder watcher (env-driven)"
> echo "  ingest          One-shot ingest via CLI (FILE=...)"
> echo "  ingest-api      One-shot ingest via HTTP JSON (FILE=...)"
> echo "  dbinit|migrate  Apply schema / ensure columns/tables"
> echo "  dbcheck         Counts: work/scene/chunk/ingest_run"
> echo "  works-ls        List works (id,title,chars)"
> echo "  works-ids       GET /v1/works/ids (q, limit, offset)"
> echo "  works-summary   GET /v1/works/summary (q, limit, offset)"
> echo "  scenes-ls       Scenes for a work (WORK_ID=...)"
> echo "  chunks-ls       Chunks for a work (WORK_ID=...)"
> echo "  slice-api       GET slice via API (WORK_ID, START, END)"
> echo "  slice-sql       SUBSTR via sqlite (WORK_ID, START, END)"
> echo "  search          GET /v1/search (Q='alpha', WORK_ID=..., LIMIT, OFFSET, REBUILD=0|1)"
> echo "  resegment-api   POST /v1/works/{id}/resegment (PROFILE, WINDOW, STRIDE)"
> echo "  test|fmt|lint   Quality tools"
> echo "  docker-build    Build image 'lore-ingest:latest'"
> echo "  docker-up/core  Compose up (all/core)"
> echo "  logs-*          Tail logs for http/worker/temporal"
> echo "  worker          Run Temporal worker locally (not via compose)"
> echo "  temporal-smoke  Run scripts/temporal_smoke.py (WORK_ID env required)"
> echo "  clean           Clear __pycache__/pyc"
> echo "  clean-dirs      Empty success/ and fail/"

whereis:
> echo "DB_PATH=$(DB_PATH)"
> echo "INBOX=$(INBOX)"
> echo "SUCCESS_DIR=$(SUCCESS_DIR)"
> echo "FAIL_DIR=$(FAIL_DIR)"
> echo "HTTP=http://$(HOST):$(PORT)"
> echo "WINDOW=$(WINDOW) STRIDE=$(STRIDE)"
> echo "ALLOWED_EXT=$(ALLOWED_EXT) MAX_FILE_MB=$(MAX_FILE_MB)"
> echo "Temporal: $(TEMPORAL_TARGET) ns=$(TEMPORAL_NAMESPACE) queue=$(INGEST_TASK_QUEUE)"
> echo -n "CLI entry (cli/main.py):        " ; [ -f cli/main.py ] && echo OK || echo MISSING
> echo -n "HTTP app (service/http_app.py): " ; [ -f service/http_app.py ] && echo OK || echo MISSING
> echo -n "Watcher (service/watcher.py):   " ; [ -f service/watcher.py ] && echo OK || echo MISSING
> echo -n "Temporal worker:                " ; [ -f service/temporal_worker.py ] && echo OK || echo MISSING
> echo -n "Schema ($(SCHEMA)):             " ; [ -f $(SCHEMA) ] && echo OK || echo MISSING

dirs:
> mkdir -p "$(INBOX)" "$(SUCCESS_DIR)" "$(FAIL_DIR)"

venv:
> $(PY) -m venv .venv && echo "==> Activate with: . .venv/bin/activate"

deps:
> $(PY) -m pip install -U pip
> if [ -f requirements.txt ]; then $(PY) -m pip install -r requirements.txt; else echo "requirements.txt not found (skipping)"; fi

dev: deps
> $(PY) -m pip install pytest ruff

# ---- Run HTTP -------------------------------------------------------------
run-http: dirs
> DB_PATH="$(DB_PATH)" INBOX="$(INBOX)" SUCCESS_DIR="$(SUCCESS_DIR)" FAIL_DIR="$(FAIL_DIR)" \
>   $(UVICORN) service.http_app:app --host "$(HOST)" --port "$(PORT)"

open:
> $(PY) -c "import webbrowser; webbrowser.open('http://$(HOST):$(PORT)/docs')"

health:
> $(CURL) "http://$(HOST):$(PORT)/v1/healthz" || echo "healthz not reachable"

ready:
> $(CURL) "http://$(HOST):$(PORT)/v1/readyz" || echo "readyz not reachable"

metrics:
> $(CURL) "http://$(HOST):$(PORT)/metrics" | head -n 50 || echo "metrics not reachable"

# ---- Watcher & CLI ingest -------------------------------------------------
watch: dirs
> DB_PATH="$(DB_PATH)" INBOX="$(INBOX)" SUCCESS_DIR="$(SUCCESS_DIR)" FAIL_DIR="$(FAIL_DIR)" \
>   ALLOWED_EXT="$(ALLOWED_EXT)" MAX_FILE_MB="$(MAX_FILE_MB)" \
>   $(PY) -m cli.main watch

ingest:
> if [ -z "$(FILE)" ]; then echo "Usage: gmake ingest FILE=./path/to/file.txt [TITLE='My Title'] [AUTHOR='Name'] [PROFILE=profile]"; exit 2; fi
> DB_PATH="$(DB_PATH)" $(PY) -m cli.main ingest "$(FILE)" \
>   --db "$(DB_PATH)" \
>   $(if $(TITLE),--title "$(TITLE)") \
>   $(if $(AUTHOR),--author "$(AUTHOR)") \
>   $(if $(PROFILE),--profile "$(PROFILE)")

ingest-api:
> if [ -z "$(FILE)" ]; then echo "Usage: gmake ingest-api FILE=./path/to/file.txt [TITLE='My Title'] [AUTHOR='Name']"; exit 2; fi
> $(CURL) -X POST "http://$(HOST):$(PORT)/v1/ingest" \
>   -H "Content-Type: application/json" \
>   -d '{"path":"$(FILE)","title":"$(TITLE)","author":"$(AUTHOR)","window_chars":$(WINDOW),"stride_chars":$(STRIDE)$(if $(PROFILE),,"")$(if $(PROFILE),,"")}' \
>   || echo "POST /v1/ingest failed (server?)"

# ---- DB ops ---------------------------------------------------------------
dbinit:
> if [ ! -f "$(SCHEMA)" ]; then echo "Schema not found: $(SCHEMA)"; exit 2; fi
> echo "==> Applying schema to $(DB_PATH)"
> $(SQLITE3) < "$(SCHEMA)" && echo "OK"

migrate:
> $(PY) - <<'PYCODE'
> from lore_ingest.persist import open_db, ensure_ingest_columns_and_tables
> conn = open_db("$(DB_PATH)")
> try:
>     ensure_ingest_columns_and_tables(conn)
>     print("Migration ensured: work.content_sha1, work.ingest_run_id, ingest_run table")
> finally:
>     conn.close()
> PYCODE

dbcheck:
> echo "works:"        ; $(SQLITE3) "SELECT COUNT(*) FROM work;"
> echo "scenes:"       ; $(SQLITE3) "SELECT COUNT(*) FROM scene;"
> echo "chunks:"       ; $(SQLITE3) "SELECT COUNT(*) FROM chunk;"
> echo "ingest_run:"   ; $(SQLITE3) "SELECT COUNT(*) FROM ingest_run;" 2>/dev/null || true

works-ls:
> $(SQLITE3) "SELECT id, COALESCE(title,''), char_count, created_at FROM work ORDER BY datetime(created_at) DESC LIMIT 20;"

scenes-ls:
> if [ -z "$(WORK_ID)" ]; then echo "Usage: gmake scenes-ls WORK_ID=<uuid>"; exit 2; fi
> $(SQLITE3) "SELECT id, idx, char_start, char_end, COALESCE(heading,'') FROM scene WHERE work_id='$(WORK_ID)' ORDER BY idx;"

chunks-ls:
> if [ -z "$(WORK_ID)" ]; then echo "Usage: gmake chunks-ls WORK_ID=<uuid>"; exit 2; fi
> $(SQLITE3) "SELECT id, scene_id, idx, char_start, char_end FROM chunk WHERE work_id='$(WORK_ID)' ORDER BY idx LIMIT 50;"

slice-api:
> if [ -z "$(WORK_ID)" ] || [ -z "$(START)" ] || [ -z "$(END)" ]; then echo "Usage: gmake slice-api WORK_ID=<uuid> START=<n> END=<n>"; exit 2; fi
> $(CURL) "http://$(HOST):$(PORT)/v1/works/$(WORK_ID)/slice?start=$(START)&end=$(END)" || echo "slice failed"

slice-sql:
> if [ -z "$(WORK_ID)" ] || [ -z "$(START)" ] || [ -z "$(END)" ]; then echo "Usage: gmake slice-sql WORK_ID=<uuid> START=<n> END=<n>"; exit 2; fi
> $(SQLITE3) "SELECT SUBSTR(norm_text, $(START)+1, $(END)-$(START)) FROM work WHERE id='$(WORK_ID)';"

# ---- Works (HTTP helpers) -------------------------------------------------
works-ids:
> $(CURL) "http://$(HOST):$(PORT)/v1/works/ids?limit=$(LIMIT)&offset=$(OFFSET)$(if $(Q),&q=$(Q))" | jq || true

works-summary:
> $(CURL) "http://$(HOST):$(PORT)/v1/works/summary?limit=$(LIMIT)&offset=$(OFFSET)$(if $(Q),&q=$(Q))" | jq || true

# ---- FTS Search -----------------------------------------------------------
search:
> if [ -z "$(Q)" ]; then echo "Usage: gmake search Q='query' [WORK_ID=<uuid>] [LIMIT=25] [OFFSET=0] [REBUILD=0|1]"; exit 2; fi
> $(CURL) "http://$(HOST):$(PORT)/v1/search?q=$(Q)$(if $(WORK_ID),&work_id=$(WORK_ID))&limit=$(LIMIT)&offset=$(OFFSET)&rebuild=$(REBUILD)" | jq || true

# ---- Resegment via API ----------------------------------------------------
resegment-api:
> if [ -z "$(WORK_ID)" ]; then echo "Usage: gmake resegment-api WORK_ID=<uuid> [PROFILE=...] [WINDOW=512] [STRIDE=384]"; exit 2; fi
> $(CURL) -X POST "http://$(HOST):$(PORT)/v1/works/$(WORK_ID)/resegment" \
>   -H "Content-Type: application/json" \
>   -d '{"profile":"$(PROFILE)","window_chars":$(WINDOW),"stride_chars":$(STRIDE)}' | jq || true

# ---- Temporal (local) -----------------------------------------------------
temporal-env:
> echo "TEMPORAL_TARGET=$(TEMPORAL_TARGET)"
> echo "TEMPORAL_NAMESPACE=$(TEMPORAL_NAMESPACE)"
> echo "INGEST_TASK_QUEUE=$(INGEST_TASK_QUEUE)"

worker:
> echo "==> Temporal worker → $(TEMPORAL_TARGET) ns=$(TEMPORAL_NAMESPACE) queue=$(INGEST_TASK_QUEUE)"
> TEMPORAL_TARGET="$(TEMPORAL_TARGET)" TEMPORAL_NAMESPACE="$(TEMPORAL_NAMESPACE)" INGEST_TASK_QUEUE="$(INGEST_TASK_QUEUE)" \
>   $(PY) -m service.temporal_worker

temporal-smoke:
> : $${WORK_ID:?Set WORK_ID from a recent /v1/ingest response}; :
> TEMPORAL_TARGET="$(TEMPORAL_TARGET)" TEMPORAL_NAMESPACE="$(TEMPORAL_NAMESPACE)" TEMPORAL_TASK_QUEUE="$(INGEST_TASK_QUEUE)" \
>   DB_PATH="$(DB_PATH)" WORK_ID="$(WORK_ID)" \
>   $(PY) scripts/temporal_smoke.py

# ---- Quality --------------------------------------------------------------
test:
> $(PY) -m pytest -q

fmt:
> ruff format .

lint:
> ruff check .

# ---- Docker ---------------------------------------------------------------
docker-build:
> docker build -t lore-ingest:latest .

docker-up:
> docker compose up -d

docker-up-core:
> docker compose up -d temporal temporal-ui temporal-worker http

docker-down:
> docker compose down

logs-http:
> docker compose logs --tail=200 http

logs-worker:
> docker compose logs --tail=200 temporal-worker

logs-temporal:
> docker compose logs --tail=200 temporal

# ---- Cleanup --------------------------------------------------------------
clean:
> find . -name "__pycache__" -type d -prune -exec rm -rf {} +
> find . -name "*.pyc" -delete
> echo "==> Cleaned __pycache__ and *.pyc"

clean-dirs:
> find "$(SUCCESS_DIR)" -mindepth 1 -maxdepth 1 -print -exec rm -rf {} +
> find "$(FAIL_DIR)" -mindepth 1 -maxdepth 1 -print -exec rm -rf {} +
> echo "==> Emptied $(SUCCESS_DIR)/ and $(FAIL_DIR)/ (kept dirs)"
