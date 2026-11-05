-- Apply to Core LAS-owned PostgreSQL only, after migrations 001-003.
-- Holds the uploaded document's bytes, encrypted at rest (see
-- app/services/dpr_agentic/upload_staging.py), only until the worker that
-- analyzes it reaches a terminal state. Never queried for content by
-- anything but that worker; deleted as soon as it's no longer needed.
CREATE TABLE IF NOT EXISTS dpr_upload_staging (
    run_id UUID PRIMARY KEY REFERENCES dpr_analysis_runs(run_id) ON DELETE CASCADE,
    ciphertext BYTEA NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
