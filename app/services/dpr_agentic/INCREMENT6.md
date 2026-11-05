# Increment 6 — Audit fixes, dependency lock, and a real report-access gate

Follows an engineering audit of Increments 3-5 against CLAUDE.md and the
actual code (not just the increment docs). See that audit for the full list
of findings; this covers what was fixed and built in response.

## Fixed

- **TLS verification leak (critical).** `GeminiAgentClient` was reusing the
  vendor's shared `httpx.AsyncClient`, which runs with `verify=vendor_verify_ssl`
  -- routinely `False` locally, per CLAUDE.md, to work around the vendor's own
  broken cert chain. That meant every Gemini call (API key + uploaded DPR
  text) silently lost TLS verification too. `app/main.py` now builds a
  separate `gemini_http_client` hardcoded to `verify=True`.
- **Dependency lock drift.** `pyproject.toml` declared `sqlalchemy`,
  `psycopg`, `langgraph`, `langgraph-checkpoint-postgres` but neither lock
  file was ever regenerated, and `pymupdf`/`openpyxl` (used in
  `document_pipeline/`) weren't declared at all. Both are fixed; a fresh,
  hash-verified install from `requirements-dev.txt` now passes the full suite.
- **`jobs.finish()` crashed on a malformed lease token** (`lease_token` is a
  `UUID` column) instead of returning `False` as its signature promises.
  Found by the new tests below; hardened to fail closed.

## Built

- **`jobs.py` / `worker.py` / `branch_status.py` now have real test
  coverage** (`tests/test_dpr_jobs.py`, `test_dpr_worker.py`,
  `test_dpr_branch_status.py`) against a disposable Postgres -- previously
  zero, despite being Increment 3/4's headline feature.
- **The durable-job Postgres is now actually wired up.** New setting
  `DPR_DATABASE_URL`; `app/main.py`'s lifespan creates the engine and applies
  all three migrations (idempotent, safe with concurrent replicas) when it's
  set and the feature is enabled. Unset: `/document-with-analysis` still
  works exactly as before (ephemeral), `/report` always `503`s. This is
  optional wiring, not a hard new dependency for the existing dashboard.
- **`/document-with-analysis` now records a durable run.** It submits a job,
  initializes branch status, and -- once analysis completes -- persists the
  report via the new `report_store.py` (migration `003_dpr_report_artifacts.sql`).
  It still never returns the report itself (`report_status` stays
  `NOT_PUBLISHED`); this is request-scoped work recorded durably, not yet a
  restart-safe background worker (see Known limits).
- **A real, gated report-download endpoint**, the thing `report_access.py`
  and `branch_status.py` existed for but had no caller:
  `GET /customer/services/{code}/report` and the BD-owned mirror
  `GET /bd/customers/{person_id}/services/{code}/report`. New
  `report_service.py` resolves the latest run for a person+service, always
  re-checks payment against the vendor at call time (never a cached flag),
  and only releases the report when `report_access.evaluate_report_access`
  says so. 404 if no analysis ever ran; 403 with machine-readable `reasons`
  if still gated; 503 if no database is configured.
- Documented in `docs/MIDDLEWARE_API.md` (new "Feature-gated: DPR Agentic
  preview" section) and the required env vars.

## Deliberately out of scope this round

- **`enrolment.py`'s payment state machine is unchanged.** Pay Later still
  settles synchronously (consents -> transfer -> Paid) before any upload is
  allowed; there is no "consented, unpaid, upload allowed" state. The
  locked-report-until-payment path `report_access.py` supports exists for a
  payment method this codebase doesn't have yet, not the current Pay Later.
- **No restart-safe worker.** `jobs.claim()`/`finish()` are now exercised by
  real tests, but nothing polls `QUEUED` rows and resumes a crashed run; that
  needs an explicit decision on where a worker process/loop lives before it's
  built (CLAUDE.md is explicit about not adding new infra, e.g. Celery,
  without justification).
- **No encrypted durable upload staging.** Only the final structured
  `AnalysisResult` is persisted; raw uploaded bytes never outlive the request.
- Pre-existing lint/format debt in `dpr_analysis.py`, `customer.py` and the
  original body of `jobs.py` (long lines, unsorted imports, unused imports)
  predates this increment and was left alone to keep the diff to what was
  agreed.
