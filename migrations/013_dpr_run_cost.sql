-- Apply to Core LAS-owned PostgreSQL only, after migrations 001-012.
--
-- The Gemini cost of each analysis run (app/services/dpr_agentic/run_cost.py),
-- next to the token totals of 008, plus the three usage figures the cost
-- needs that 008 did not keep: thinking tokens (billed as output), tokens
-- served from the context cache (billed at the cache rate) and Google
-- Search grounding queries (billed per query).
--
-- cost_usd is computed when the run finishes, at the rates configured then
-- (DPR_GEMINI_PRICE_*), and never recomputed: Gemini's rates change, and a
-- run's cost is what it cost when it ran. The four rates are stored with it
-- so a figure can always be explained. NULL when the rates were not
-- configured; cost_inr is NULL unless DPR_USD_TO_INR was set too. Pre-013
-- runs stay NULL (their per-call usage was never persisted).
ALTER TABLE dpr_analysis_runs
    ADD COLUMN IF NOT EXISTS thinking_tokens INTEGER,
    ADD COLUMN IF NOT EXISTS cached_tokens INTEGER,
    ADD COLUMN IF NOT EXISTS search_queries INTEGER,
    ADD COLUMN IF NOT EXISTS gemini_model TEXT,
    ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(14, 6),
    ADD COLUMN IF NOT EXISTS cost_inr NUMERIC(14, 4),
    ADD COLUMN IF NOT EXISTS usd_to_inr NUMERIC(10, 4),
    ADD COLUMN IF NOT EXISTS price_rates JSONB;
