-- Apply to a Core LAS owned PostgreSQL database, NOT Wallet or LAS_Email.
-- A deployment-specific secret manager must protect all document content.
CREATE TABLE IF NOT EXISTS dpr_analysis_runs (
    run_id UUID PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    service_request_id TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    document_sha256 CHAR(64) NOT NULL,
    analysis_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('QUEUED','RUNNING','COMPLETED','FAILED')),
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_token UUID,
    lease_until TIMESTAMPTZ,
    artifact_ref TEXT,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dpr_runs_pending ON dpr_analysis_runs(status, lease_until);
CREATE INDEX IF NOT EXISTS idx_dpr_runs_sr ON dpr_analysis_runs(service_request_id);
