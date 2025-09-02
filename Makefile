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

TEMPORAL_TARGET    ?= localhost:7233
TEMPORAL_NAMESPACE ?= default
INGEST_TASK_QUEUE  ?= ingest-queue


# --- Sanitize config (strip stray whitespace) ------------------------------
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

# ---- Helpers --------------------------------------------------------------
SQLITE3 := sqlite3 "$(DB_PATH)"
CURL    := curl -fsS

# ---- Phony ----------------------------------------------------------------
.PHONY: help whereis dirs dev venv deps \
        run-http open health \
        watch ingest ingest-api \
        dbinit migrate dbcheck works-ls scenes-ls chunks-ls slice-api slice-sql \
        test fmt lint \
        docker-build docker-up docker-down \
        clean clean-dirs \
        worker

help:
> echo "Targets:"
> echo "  whereis         Show config (DB/paths/HTTP) and availability hints"
> echo "  dirs            Create $(INBOX) $(SUCCESS_DIR) $(FAIL_DIR)"
> echo "  dev             Install dev deps (pytest, etc.)"
> echo "  run-http        Start FastAPI (UVICORN) on $(HOST):$(PORT)"
> echo "  open            Open Swagger UI at http://$(HOST):$(PORT)/docs"
> echo "  health          GET /v1/healthz"
> echo "  watch           Start folder watcher (env-driven)"
> echo "  ingest          One-shot ingest via CLI (FILE=<path> [TITLE=] [AUTHOR=])"
> echo "  ingest-api      One-shot ingest via HTTP JSON (server must be running)"
> echo "  dbinit          Apply schema from $(SCHEMA) to $(DB_PATH)"
> echo "  migrate         Ensure content_sha1 & ingest_run exist (safe/no-op if present)"
> echo "  dbcheck         Quick counts: work/scene/chunk/ingest_run"
> echo "  works-ls        List recent works (id, title, chars)"
> echo "  scenes-ls       List scenes for a work (WORK_ID=<uuid>)"
> echo "  chunks-ls       List chunks for a work (WORK_ID=<uuid>)"
> echo "  slice-api       GET /v1/works/{id}/slice?start=&end= (need WORK_ID, START, END)"
> echo "  slice-sql       SUBSTR(norm_text, ...) via sqlite (need WORK_ID, START, END)"
> echo "  test            Run pytest"
> echo "  fmt             Format code (ruff)"
> echo "  lint            Lint code (ruff)"
> echo "  docker-build    Build image 'lore-ingest:latest'"
> echo "  docker-up       docker compose up (http + watcher)"
> echo "  docker-down     docker compose down"
> echo "  clean           Remove __pycache__ and pyc files"
> echo "  clean-dirs      Empty $(SUCCESS_DIR) and $(FAIL_DIR) (keeps dirs)"

whereis:
> echo "DB_PATH=$(DB_PATH)"
> echo "INBOX=$(INBOX)"
> echo "SUCCESS_DIR=$(SUCCESS_DIR)"
> echo "FAIL_DIR=$(FAIL_DIR)"
> echo "HTTP=http://$(HOST):$(PORT)"
> echo "WINDOW=$(WINDOW) STRIDE=$(STRIDE)"
> echo "ALLOWED_EXT=$(ALLOWED_EXT) MAX_FILE_MB=$(MAX_FILE_MB)"
> echo -n "CLI entry (cli/main.py):        " ; [ -f cli/main.py ] && echo OK || echo MISSING
> echo -n "HTTP app (service/http_app.py): " ; [ -f service/http_app.py ] && echo OK || echo MISSING
> echo -n "Watcher (service/watcher.py):   " ; [ -f service/watcher.py ] && echo OK || echo MISSING
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

# ---- Watcher & CLI ingest -------------------------------------------------
watch: dirs
> DB_PATH="$(DB_PATH)" INBOX="$(INBOX)" SUCCESS_DIR="$(SUCCESS_DIR)" FAIL_DIR="$(FAIL_DIR)" \
>   ALLOWED_EXT="$(ALLOWED_EXT)" MAX_FILE_MB="$(MAX_FILE_MB)" \
>   $(PY) -m cli.main watch

ingest:
> if [ -z "$(FILE)" ]; then echo "Usage: gmake ingest FILE=./path/to/file.txt [TITLE='My Title'] [AUTHOR='Name']"; exit 2; fi
> DB_PATH="$(DB_PATH)" $(PY) -m cli.main ingest "$(FILE)" \
>   --db "$(DB_PATH)" \
>   --window $(WINDOW) --stride $(STRIDE) \
>   $(if $(TITLE),--title "$(TITLE)") \
>   $(if $(AUTHOR),--author "$(AUTHOR)")

ingest-api:
> if [ -z "$(FILE)" ]; then echo "Usage: gmake ingest-api FILE=./path/to/file.txt [TITLE='My Title'] [AUTHOR='Name']"; exit 2; fi
> $(CURL) -X POST "http://$(HOST):$(PORT)/v1/ingest" \
>   -H "Content-Type: application/json" \
>   -d '{"path":"$(FILE)","title":"$(TITLE)","author":"$(AUTHOR)","window_chars":$(WINDOW),"stride_chars":$(STRIDE)}' \
>   || echo "POST /v1/ingest failed (is the server running?)"

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
> $(SQLITE3) "SELECT id, COALESCE(title,''), char_count, created_at FROM work ORDER BY created_at DESC LIMIT 20;"

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

worker:
> echo "==> Temporal worker → $(TEMPORAL_TARGET) ns=$(TEMPORAL_NAMESPACE) queue=$(INGEST_TASK_QUEUE)"
> TEMPORAL_TARGET="$(TEMPORAL_TARGET)" TEMPORAL_NAMESPACE="$(TEMPORAL_NAMESPACE)" INGEST_TASK_QUEUE="$(INGEST_TASK_QUEUE)" \
>   python3 -m service.temporal_worker

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

docker-down:
> docker compose down

# ---- Cleanup --------------------------------------------------------------
clean:
> find . -name "__pycache__" -type d -prune -exec rm -rf {} +
> find . -name "*.pyc" -delete
> echo "==> Cleaned __pycache__ and *.pyc"

clean-dirs:
> find "$(SUCCESS_DIR)" -mindepth 1 -maxdepth 1 -print -exec rm -rf {} +
> find "$(FAIL_DIR)" -mindepth 1 -maxdepth 1 -print -exec rm -rf {} +
> echo "==> Emptied $(SUCCESS_DIR)/ and $(FAIL_DIR)/ (kept dirs)"
