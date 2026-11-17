-- Apply to Core LAS-owned PostgreSQL only, after migrations 001-013.
--
-- Writing a finished DPR report back to the wallet (user, 2026-11-16): the
-- person's service status becomes "Report ready", the PDF goes to the
-- service's report custom field (DPR_ANALYSIS_Report), and the BD is emailed. The DPR worker never
-- calls the vendor (it holds no token), so Core's intake worker does it,
-- once per report version (app/services/intake/reports.py).

CREATE TABLE IF NOT EXISTS dpr_report_wallet_sync (
    run_id UUID PRIMARY KEY REFERENCES dpr_analysis_runs(run_id) ON DELETE CASCADE,
    source_reference TEXT NOT NULL,
    version INT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'DONE', 'FAILED')),
    attempts INT NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- What was written: REPORT_STORED, or STATUS_ONLY (no _Report field on
    -- this person, or no PDF), and whether the BD was emailed.
    outcome TEXT,
    bd_emailed BOOLEAN NOT NULL DEFAULT false,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS dpr_report_wallet_sync_due
    ON dpr_report_wallet_sync (status, next_attempt_at);
