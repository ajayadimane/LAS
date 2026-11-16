-- Apply to Core LAS-owned PostgreSQL only, after migrations 001-011.
--
-- 1. The DPR Analysis service details (bank name, project name) captured at
--    upload, staged with the document so the worker can give them to the
--    analysis (AnalysisInput.project_context). Fernet-encrypted JSON like the
--    staged bytes (a project name can contain a person's name); deleted with
--    the staging row.
ALTER TABLE dpr_upload_staging
    ADD COLUMN IF NOT EXISTS project_context_ciphertext BYTEA;

-- 2. Grounded web-search results (app/services/dpr_agentic/grounding.py),
--    keyed by a hash of the non-identifying search brief (bank, scheme,
--    sector, state, loan band). Public information, reused for
--    DPR_GROUNDING_CACHE_TTL_DAYS so the same project gets the same evidence
--    and search is paid once; purged by the worker's poll loop.
CREATE TABLE IF NOT EXISTS dpr_grounding_cache (
    cache_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    statements JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS dpr_grounding_cache_expires_at
    ON dpr_grounding_cache (expires_at);
