Add failure events to the watcher (oversized/unsupported/parse errors).

Force resegment endpoint/CLI (/v1/works/:id/resegment, lore-ingest resegment) to re-split existing works with a different profile without changing bytes.

Prometheus metrics (/metrics) and simple dashboards.

FTS search endpoint over chunk_fts.

Concurrency for the watcher (--workers, backoff, and subdir scanning).