-- Apply to Core LAS-owned PostgreSQL only, after migrations 001-005.
--
-- Re-analysis after DPR corrections submits a brand-new run (a new
-- document_sha256 makes jobs.submit()'s idempotency_key different, so it is
-- never the same run_id -- see report_service.py's latest_run_for(), keyed
-- on source_reference, not run_id). Versioning scoped to run_id therefore
-- always started back at v1 for a corrected re-run, losing history. This
-- migration rescopes versions to source_reference (the person+service),
-- the same key latest_run_for() already uses to span runs, so a correction
-- continues as v2, v3, ... across run_ids.
--
-- Also adds:
--  * snapshot_sha256 + UNIQUE (run_id, snapshot_sha256): storing the same
--    run_id with the same report content twice is now idempotent --
--    store_report() returns the existing version instead of appending one.
--  * render_status (PENDING/RENDERED/FAILED): get_report() defaults to the
--    latest version with render_status='RENDERED', so a version that never
--    finished rendering is not served as "the" report.
--  * a trigger making every row immutable (no UPDATE, no DELETE) except a
--    strictly-forward render_status transition -- PENDING may become
--    RENDERED or FAILED; neither is reversible or lateral.
ALTER TABLE dpr_report_artifacts
    ADD COLUMN IF NOT EXISTS source_reference TEXT,
    ADD COLUMN IF NOT EXISTS snapshot_sha256 TEXT,
    ADD COLUMN IF NOT EXISTS render_status TEXT NOT NULL DEFAULT 'PENDING';

UPDATE dpr_report_artifacts a
SET source_reference = r.source_reference
FROM dpr_analysis_runs r
WHERE a.run_id = r.run_id AND a.source_reference IS NULL;

-- Pre-006 rows have no snapshot hash recorded; a unique, non-colliding
-- placeholder per row lets the uniqueness constraint below apply without a
-- backfill collision (a real hash is always 64 lowercase hex chars, which
-- this can never match).
UPDATE dpr_report_artifacts
SET snapshot_sha256 = 'legacy-' || run_id::text || '-' || version::text
WHERE snapshot_sha256 IS NULL;

-- Pre-006 rows were already a completed, servable report under the old
-- scheme; mark them RENDERED so get_report()'s new default filter does not
-- hide history that was previously visible.
UPDATE dpr_report_artifacts SET render_status = 'RENDERED' WHERE render_status = 'PENDING';

ALTER TABLE dpr_report_artifacts
    ALTER COLUMN source_reference SET NOT NULL,
    ALTER COLUMN snapshot_sha256 SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'dpr_report_artifacts_render_status_check'
    ) THEN
        ALTER TABLE dpr_report_artifacts
            ADD CONSTRAINT dpr_report_artifacts_render_status_check
            CHECK (render_status IN ('PENDING', 'RENDERED', 'FAILED'));
    END IF;
END $$;

-- The old PK was (run_id, version); versions are now scoped to
-- source_reference, so that pair is no longer unique by construction. Drop
-- it for a surrogate key and the two constraints versioning actually needs.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'dpr_report_artifacts_pkey'
          AND conrelid = 'dpr_report_artifacts'::regclass
          AND array_length(conkey, 1) = 2
    ) THEN
        ALTER TABLE dpr_report_artifacts DROP CONSTRAINT dpr_report_artifacts_pkey;
    END IF;
END $$;

ALTER TABLE dpr_report_artifacts
    ADD COLUMN IF NOT EXISTS id BIGSERIAL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'dpr_report_artifacts_pkey'
    ) THEN
        ALTER TABLE dpr_report_artifacts ADD PRIMARY KEY (id);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'dpr_report_artifacts_run_snapshot_key'
    ) THEN
        ALTER TABLE dpr_report_artifacts
            ADD CONSTRAINT dpr_report_artifacts_run_snapshot_key UNIQUE (run_id, snapshot_sha256);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'dpr_report_artifacts_source_version_key'
    ) THEN
        ALTER TABLE dpr_report_artifacts
            ADD CONSTRAINT dpr_report_artifacts_source_version_key UNIQUE (source_reference, version);
    END IF;
END $$;

DROP INDEX IF EXISTS idx_dpr_report_artifacts_run_id_version;
CREATE INDEX IF NOT EXISTS idx_dpr_report_artifacts_source_ref_version
    ON dpr_report_artifacts (source_reference, version DESC);

CREATE OR REPLACE FUNCTION dpr_report_artifacts_immutable() RETURNS trigger AS $$
DECLARE
    rank jsonb := '{"PENDING": 0, "RENDERED": 1, "FAILED": 1}'::jsonb;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'dpr_report_artifacts rows are immutable: DELETE is not allowed (source_reference=%, version=%)',
            OLD.source_reference, OLD.version;
    END IF;

    IF OLD.run_id IS DISTINCT FROM NEW.run_id
        OR OLD.source_reference IS DISTINCT FROM NEW.source_reference
        OR OLD.version IS DISTINCT FROM NEW.version
        OR OLD.snapshot_sha256 IS DISTINCT FROM NEW.snapshot_sha256
        OR OLD.report IS DISTINCT FROM NEW.report
        OR OLD.created_at IS DISTINCT FROM NEW.created_at
    THEN
        RAISE EXCEPTION
            'dpr_report_artifacts rows are immutable except render_status (source_reference=%, version=%)',
            OLD.source_reference, OLD.version;
    END IF;

    IF OLD.render_status IS DISTINCT FROM NEW.render_status
        AND (rank->>OLD.render_status)::int >= (rank->>NEW.render_status)::int
    THEN
        RAISE EXCEPTION
            'render_status may only move forward (% -> % is not forward, source_reference=%, version=%)',
            OLD.render_status, NEW.render_status, OLD.source_reference, OLD.version;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_dpr_report_artifacts_immutable ON dpr_report_artifacts;
CREATE TRIGGER trg_dpr_report_artifacts_immutable
    BEFORE UPDATE OR DELETE ON dpr_report_artifacts
    FOR EACH ROW EXECUTE FUNCTION dpr_report_artifacts_immutable();
