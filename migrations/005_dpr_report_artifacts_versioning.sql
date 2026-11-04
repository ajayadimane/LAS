-- Apply to Core LAS-owned PostgreSQL only, after migrations 001-004.
-- Append-only report versioning: a run_id may now have more than one
-- stored report (e.g. a redundant worker finishing a reclaimed run after
-- another worker already stored a report for it -- see report_store.py).
-- Every store appends a new version; no row is ever updated or deleted,
-- so a previous report version can never be overwritten (Section 16).
ALTER TABLE dpr_report_artifacts
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'dpr_report_artifacts_pkey'
          AND conrelid = 'dpr_report_artifacts'::regclass
          AND array_length(conkey, 1) = 2
    ) THEN
        ALTER TABLE dpr_report_artifacts DROP CONSTRAINT dpr_report_artifacts_pkey;
        ALTER TABLE dpr_report_artifacts ADD PRIMARY KEY (run_id, version);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_dpr_report_artifacts_run_id_version
    ON dpr_report_artifacts (run_id, version DESC);
