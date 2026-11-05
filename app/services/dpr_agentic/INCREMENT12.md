# Increment 12 — Phase F: operational completeness

Phase F of the production-grade plan -- the last phase (Phase A:
INCREMENT7.md; Phase B: INCREMENT8.md; Phase C: INCREMENT9.md; Phase D:
INCREMENT10.md; Phase E: INCREMENT11.md).

## What changed

- **New `.env.example` at the repo root.** It didn't exist at all in this
  checkout -- not even for pre-DPR settings, despite `deploy/DEPLOYMENT.md`
  already referencing it as if it did. Covers every `Settings` field in
  `app/core/config.py`, not just the DPR additions: vendor, mail, onboarding,
  payment-field-value, and all `dpr_agentic_*`/`dpr_database_url`/
  `dpr_staging_encryption_key` settings, with which ones are required vs.
  have a working default clearly marked. Verified by actually loading it as
  a real `.env` and confirming `Settings()` parses every value exactly as
  documented, both with the DPR settings left at their commented-out
  defaults and with them filled in (including a real Fernet key passing the
  existing validator).
- **Per-run timing**: `scripts/dpr_worker.py` now logs
  `dpr_run_completed`/`dpr_run_failed` with `run_id` and `duration_ms`,
  so one analysis can be traced end to end in the logs by its run id.
- **Per-call token/cost visibility**: `GeminiAgentClient` now logs
  `dpr_gemini_call` with the model name and prompt/completion/total token
  counts after every request (`generate`/`extract`/`ocr_page` all share the
  same `_call_with_parts`) -- numbers only, the basis for cost tracking,
  never content. This is new logging code with no prior test coverage of
  its own (the existing fakes bypass `GeminiAgentClient` entirely), so it's
  covered by a dedicated test that exercises the real client against a
  mocked HTTP response and confirms the canary content in both the prompt
  and the response never reaches the logs, only the numbers.
- **Pre-existing lint/format debt cleaned up**, scoped exactly as the plan
  called for: `app/api/v1/dpr_analysis.py`, `app/api/v1/customer.py` and
  `app/services/dpr_agentic/jobs.py` are now fully `ruff check`/`ruff
  format` clean. Pure reformatting -- no behavior change, confirmed by the
  full suite passing identically before and after.

## Deliberately left alone

`ruff check .` still reports ~127 issues across other pre-existing
Increment 3-5 files this plan never asked to touch (`parallel_graph.py`,
`live_upload.py`, `report_access.py`, `branch_status.py`,
`document_pipeline/*.py`, `worker.py`'s original body, and their test
files). Reformatting all of it would be a much larger, separate diff
than "Phase F cleanup" was ever scoped to cover, and several of those files
carry carefully-worded business-logic comments where a blanket
reformat deserves its own dedicated review pass, not a drive-by one here.

## Verified

- 428 tests passing (was 427): 1 new test for `GeminiAgentClient`'s logging.
- Fresh, hash-verified install passes the full suite; no new dependencies
  this phase.
- `.env.example` independently verified by loading it as a real `.env`.

## Where this leaves the six-phase plan

All of Phases A-F from the original production-grade plan are now done:
async pipeline + standalone worker, deterministic financial verification,
vision fallback for scanned documents, PDF rendering, security hardening,
and this operational-completeness pass. What's still genuinely unbuilt,
honestly stated:

- No lease renewal for a run that outlives its worker lease.
- No PDF template polish -- it's a straightforward layout, not a
  bank-submission-ready document.
- No scheme/policy compliance checking (still no real policy corpus
  attached) and no sector-feasibility/market-research grounding beyond LLM
  judgment -- financial reconciliation and DSCR are the only deterministic
  checks that exist.
- The internal `/inspect-upload` debug endpoint and `live_upload.py`
  (now dead code in the live API, superseded by the async worker path)
  never got vision recovery; only the worker path has it.
- Gemini token-cost logging isn't correlated to a specific `run_id` yet.
- The ~127 lint issues in the files this phase deliberately left alone.
