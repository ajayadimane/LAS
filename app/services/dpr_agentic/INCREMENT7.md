# Increment 7 — Phase A: asynchronous pipeline + standalone worker

Phase A of the production-grade plan (see the full plan: deterministic
financial verification, vision fallback, PDF rendering, security hardening
and operational completeness are Phases B-F, not done here). Supersedes two
things INCREMENT6.md listed as "deliberately out of scope": there is now a
real worker and encrypted upload staging.

## What changed

- **`/document-with-analysis` no longer runs analysis inline.** It still does
  the Wallet document write synchronously (unchanged, fast, already worked),
  then stages the bytes (encrypted) and submits a durable job, returning
  `202` with `{wallet_storage_status, analysis_status: "QUEUED", run_id}`
  immediately. No more Gemini call inside the HTTP request.
- **New `scripts/dpr_worker.py`** -- a standalone process, run separately
  from the API (see `deploy/las-dpr-worker.service`). Polls for claimable
  jobs (`jobs.list_claimable`), runs `inspect_document()` then the existing
  `worker.run_claimed_analysis()` (LangGraph pipeline, unchanged), and
  deletes the staged bytes once the run reaches a terminal state. Never
  calls the vendor -- only Gemini and the DPR Postgres -- which is why the
  Wallet write had to stay synchronous: a worker running minutes or hours
  later can't be handed a still-valid vendor bearer token.
- **New `app/services/dpr_agentic/upload_staging.py`** (migration
  `004_dpr_upload_staging.sql`): the uploaded bytes, Fernet-encrypted at
  rest, keyed by `run_id`, deleted as soon as the worker is done with them
  (success or permanent failure) -- never kept longer than needed. New
  setting `DPR_STAGING_ENCRYPTION_KEY`.
- **`jobs.py` gained `list_claimable()`** (the worker needs to discover
  `run_id`s; `claim()` itself requires one already in hand) **and
  `reap_exhausted()`** -- closes a real gap: a `RUNNING` row whose lease
  expired *and* whose attempts were already used up could never be reclaimed
  *or* marked `FAILED` by the existing code, so its staged upload would have
  leaked forever. `reap_exhausted()` marks those `FAILED` so the worker can
  clean them up.
- **`worker.run_claimed_analysis()` gained an optional `lease_seconds`**
  (default unchanged at 900s, backward compatible); the worker itself uses
  1800s, since the current 15-minute default is tight once Phase B/C add
  more LLM calls per document.
- **`/report`'s 403 body now includes `status`** (`QUEUED`/`RUNNING`/
  `FAILED`/`COMPLETED`) so the dashboard can poll the one endpoint for both
  "is it ready" and "give it to me" -- no separate status endpoint needed.
  `report_service.access_report()` now returns the run row alongside the
  gate decision for this.

## Verified

- 379 tests passing (was 369), including a true end-to-end test
  (`tests/test_dpr_report_access.py::test_upload_queues_then_worker_completes_and_report_releases`)
  that uploads through the real HTTP route, confirms `/report` is locked
  with `status: QUEUED` before any worker runs, then calls the **real**
  `scripts/dpr_worker.py` functions (only `inspect_document` stubbed; the
  actual LangGraph pipeline runs against a fake LLM) and confirms the report
  releases afterward and the staged bytes are gone.
- `scripts/dpr_worker.py --once` manually run against the disposable test
  Postgres: starts and exits cleanly with no jobs queued; exits with a clear
  error listing exactly which required settings are missing when run
  unconfigured.
- Fresh, hash-verified install (`pip install --require-hashes -r
  requirements-dev.txt`) passes the full suite -- `cryptography` added to
  `pyproject.toml` and re-locked.

## Still not done (next: Phase B)

- No deterministic financial verification -- unchanged from before this
  increment.
- No vision/OCR fallback: a scanned document's run is marked `FAILED` with
  error code `NEEDSVISIONFALLBACK` rather than recovered.
- No lease renewal for a run that outlives its 1800s lease -- it will be
  reclaimed from scratch (up to 4 attempts total) rather than resumed.
- No PDF rendering; the report is still the structured JSON.
