# Increment 13 — closing out the remaining gaps from Phases A-F

Follows INCREMENT12.md's "Where this leaves the six-phase plan" list. Three
of the six items there are now explicitly out of scope by user decision (no
real policy/scheme corpus or research data source exists to ground retrieval
on; PDF branding/template polish needs design assets this session doesn't
have). The other three are done here.

## What changed

- **Lease renewal.** New `jobs.renew_lease(engine, run_id, token, *,
  lease_seconds)` extends `lease_until` for a `RUNNING` row this worker still
  holds, with the same ownership check as `finish()`.
  `worker.run_claimed_analysis()` gained an optional `heartbeat_interval`
  (default `None`, so every existing caller is unaffected); when set, it
  spawns a background task that calls `renew_lease()` repeatedly until the
  run finishes, cancelled in a `finally` either way.
  `scripts/dpr_worker.py` passes `heartbeat_interval=LEASE_SECONDS // 3`
  (600s). Previously a run whose analysis took longer than its 1800s lease
  could be reclaimed by a second worker mid-run; now only a worker that
  actually stops renewing (i.e. crashes) is ever reclaimed.
- **Removed `live_upload.py`** and `tests/test_dpr_live_upload.py` -- dead
  code since Phase A's async rewrite moved the live upload path to
  stage-and-queue instead of request-scoped parallel execution. Confirmed by
  grep that nothing else imported it. `upload_fanout.py` stays: it has its
  own independent test coverage and is a small, generically reusable
  utility, even though it's lost its only production caller.
- **Gemini cost logging now correlates to `run_id`.** New
  `gemini.CURRENT_RUN_ID: ContextVar[str | None]`; `scripts/dpr_worker.py`'s
  `process_one()` sets it once per run and resets it in a `finally`. No
  signature changes anywhere in `orchestrator.py`/`parallel_graph.py`/
  `worker.py` -- asyncio tasks copy the current context at creation time, so
  every LangGraph node sees the value. `None` for a call made outside a
  worker run (the internal `/analyze`/`/inspect-upload` endpoints, which
  still run inline).
- **All remaining pre-existing lint/format debt cleaned up** -- `ruff check .`
  and `ruff format --check .` now report zero issues across the entire
  project, not just the `dpr_agentic` module. The highest-risk part of this
  was reformatting `orchestrator.py`'s ten long `PROMPTS` strings (the
  actual LLM system prompts) into wrapped adjacent-string-literal form;
  every one was verified programmatically, byte-for-byte, against the
  original single-line text before and after, as was the `disclaimer`
  field's default string in `schemas.py`.

## Verified

- 432 tests passing (427 baseline for this round, +4 `renew_lease` unit
  tests, +1 heartbeat integration test, +2 Gemini run-id-correlation tests,
  -4 removed `live_upload.py` tests -- net +5 after accounting for removal).
- `ruff check .` / `ruff format --check .`: zero issues, project-wide, for
  the first time this session.
- Fresh, hash-verified install passes the full suite; no new dependencies.
- Every `PROMPTS` entry and the `disclaimer` default verified programmatically
  byte-identical before/after the lint cleanup -- these are the two places
  in this module where a reformatting mistake could silently change actual
  behavior (what the LLM is told, what the API claims as its disclaimer).

## Where this actually leaves things

Everything from the original six-phase plan is now either done or an
explicit, user-confirmed decision to leave out:

- **Done:** async pipeline + worker, deterministic financial verification,
  vision fallback, PDF rendering, security hardening, `.env.example` +
  observability, lease renewal, dead-code removal, cost/run correlation,
  full lint cleanup.
- **Explicitly out of scope:** real policy/scheme compliance grounding and
  market-research grounding (no real corpus or data source exists to build
  this against -- would need actual policy documents and a decision on a
  research data source before any of this could be designed honestly); PDF
  branding/template polish (a design decision needing assets this session
  doesn't have).
- **Still true, not addressed by this plan:** the internal `/inspect-upload`
  debug endpoint has no vision recovery (only the worker path does); no
  encrypted durable staging beyond the worker's own upload window; sector
  feasibility and market research remain LLM judgment with no deterministic
  check, same as financial reconciliation and DSCR were before Phase B.
