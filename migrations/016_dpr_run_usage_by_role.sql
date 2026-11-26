-- Apply to Core LAS-owned PostgreSQL only, after migrations 001-015.
--
-- Per-step usage of each analysis run (run_cost.usage_by_role): for every
-- role (financial_* extraction groups, vision_ocr, document, finance,
-- sector, risk, verifier, correction, research, policy, ...) its calls,
-- token counts, search queries and -- when rates are configured -- its
-- cost in USD at the same rates as cost_usd. Token counts only, never
-- content. Shows which step a run's cost came from: a scanned DPR's vision
-- OCR, a grounding cache miss, or a truncation retry. NULL on earlier runs.
ALTER TABLE dpr_analysis_runs
    ADD COLUMN IF NOT EXISTS usage_by_role JSONB;
