-- Apply to Core LAS-owned PostgreSQL only, after migrations 001-007.
--
-- Persists the end-to-end Gemini token total for a run (gemini.py already
-- logs this per-call via CURRENT_RUN_USAGE/dpr_gemini_call, and
-- scripts/dpr_worker.py logs the run-level rollup as dpr_run_token_usage --
-- this makes that rollup queryable, not just grep-able in logs). Nullable:
-- every pre-008 row never had usage tracked and stays NULL forever (never
-- backfilled -- the per-call data behind it was never persisted either).
-- A post-008 run always gets a real value, including 0 for one that failed
-- before any Gemini call (e.g. a config error) -- 0 and "never tracked"
-- (NULL) are deliberately distinct.
ALTER TABLE dpr_analysis_runs
    ADD COLUMN IF NOT EXISTS gemini_calls INTEGER,
    ADD COLUMN IF NOT EXISTS prompt_tokens INTEGER,
    ADD COLUMN IF NOT EXISTS completion_tokens INTEGER,
    ADD COLUMN IF NOT EXISTS total_tokens INTEGER;
