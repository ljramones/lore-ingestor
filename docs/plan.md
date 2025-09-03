
## 8) Temporal integration stub

**What:** Light activity `IngestActivity(path|bytes)` using library; workflow kick‐off after event.

* Worker container with proper retries & idempotency keyed by `content_sha1`

**Acceptance:** A demo workflow runs in the Temporal UI after an ingest.

> Say “**Ship Temporal**” and I’ll add worker code + compose service.

---

### Suggested order

1. **Events**, 2) **UX API polish**, 3) **Observability**, 4) **Force resegment**, then 5) **Watcher concurrency**.

Tell me which one to ship first (e.g., “**Ship events**”), and I’ll drop in full files + exact commands to verify.
