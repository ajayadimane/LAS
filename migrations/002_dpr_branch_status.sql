-- Apply to Core LAS-owned PostgreSQL only, after migration 001.
-- This table tracks independent execution branches, not payment authority.
CREATE TABLE IF NOT EXISTS dpr_analysis_branch_status (
    run_id UUID PRIMARY KEY REFERENCES dpr_analysis_runs(run_id) ON DELETE CASCADE,
    wallet_storage_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (wallet_storage_status IN ('PENDING','STORED','RETRY','FAILED')),
    wallet_reference TEXT,
    wallet_last_error_code TEXT,
    wallet_next_retry_at TIMESTAMPTZ,
    report_status TEXT NOT NULL DEFAULT 'QUEUED'
        CHECK (report_status IN ('QUEUED','RUNNING','COMPLETED','FAILED')),
    report_artifact_ref TEXT,
    document_retention_status TEXT NOT NULL DEFAULT 'UNKNOWN'
        CHECK (document_retention_status IN ('UNKNOWN','COMPLIANT','NONCOMPLIANT')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (wallet_storage_status <> 'STORED' OR wallet_reference IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_dpr_wallet_retries
    ON dpr_analysis_branch_status(wallet_next_retry_at)
    WHERE wallet_storage_status = 'RETRY';
-- Payment truth is intentionally not recorded here: confirm it against Wallet
-- through the authorized Core LAS service on EVERY report access request.
