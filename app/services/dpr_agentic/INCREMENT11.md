# Increment 11 — Phase E: security hardening

Phase E of the production-grade plan (Phase A: INCREMENT7.md; Phase B:
INCREMENT8.md; Phase C: INCREMENT9.md; Phase D: INCREMENT10.md). Phase F
(operational completeness) is not done here.

## What changed

- **Fixed a real integrity gap, found while writing the adversarial tests
  this phase asked for**: `verify_evidence()` set `verification` on every
  narrative finding but never touched `provenance`, so a Gemini role could
  label its own ordinary finding `provenance: "calculated"` and borrow the
  "code-verified" credibility that label is supposed to mean something
  (only `financial_calc.py`'s own code should ever set it). `verify_evidence()`
  now downgrades any narrative finding claiming `"calculated"` to
  `"unverified"` before it reaches the result -- genuine calculated findings
  from `financial_calc.py` are unaffected, since they're merged in
  separately and never pass through `generate()`/`verify_evidence()` at all.
- **New `tests/test_dpr_prompt_injection.py`**: proves the provenance fix
  above; proves a hallucinated (not-on-the-page) citation is never
  `source_verified`; proves the `disclaimer` field cannot be overridden by
  any LLM output (it's a fixed schema default, never set from `generate()`
  output); and explicitly documents a real boundary -- if the *document
  itself* contains a false or injected claim and an agent quotes it verbatim
  with the right page number, that finding correctly becomes
  `source_verified`. The guarantee is "this text is really on page N," never
  "this text is true" -- a human still has to judge whether the document
  itself is trustworthy. This is working as designed, not a gap, and the
  test exists so nobody "fixes" it into masking injected document content.
- **New `tests/test_dpr_log_safety.py`**: confirmed by code inspection first
  (grepped every `logger.*` call in `app/services/dpr_agentic/` and
  `scripts/dpr_worker.py` -- none interpolate page text, LLM responses, or
  secrets; `orchestrator.py`/`parallel_graph.py`/`gemini.py` have no logging
  at all), then pinned with regression tests using `caplog` and a canary
  string embedded in both page text and a fake LLM's output, run through
  both the sequential orchestrator path and the worker path, including the
  worker's failure-logging branch (only the exception *type name* may be
  logged, never its message -- an exception message could itself echo
  document content).
- **New per-customer daily submission cap**
  (`DPR_AGENTIC_DAILY_SUBMISSION_LIMIT`, default 5): `jobs.count_recent_submissions()`
  counts this customer's runs across all their services in the last 24h
  before `/document-with-analysis` queues a new one. Never blocks the
  Wallet write -- only whether analysis gets queued
  (`analysis_status: "RATE_LIMITED"` instead of `"QUEUED"`, same shape as
  the existing `"NOT_QUEUED"` case for no database configured).

## Verified

- 427 tests passing (was 418): 5 prompt-injection tests, 3 log-safety tests,
  1 rate-limit test.
- Fresh, hash-verified install passes the full suite -- no new dependencies
  this phase.

## Still not done (Phase F)

- `.env.example` for every `dpr_agentic_*`/`dpr_database_url`/vision/rate
  limit setting -- still undocumented even for settings that predate this
  increment.
- No per-run timing/cost logging keyed by `run_id`.
- Pre-existing lint/format debt in `dpr_analysis.py`/`customer.py`/`jobs.py`'s
  original body, noted since Increment 6, still untouched.
