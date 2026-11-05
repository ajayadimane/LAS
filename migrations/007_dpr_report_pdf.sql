-- Apply to Core LAS-owned PostgreSQL only, after migrations 001-006.
--
-- Wires the six-page CAM report (cam_render.py) into the versioned store so
-- GET .../report?format=pdf serves pre-rendered bytes instead of rendering
-- on every request. Rendering happens once, at store_report() time (see
-- report_store.py), using only the already-validated AnalysisResult -- the
-- worker that calls store_report() never calls the vendor (see
-- scripts/dpr_worker.py's module docstring), so there is no borrower/project
-- profile to render with; the CAM templates already show an explicit
-- missing-data state for that (BorrowerProfile()/ProjectProfile() defaults),
-- by the same anti-fabrication discipline as everywhere else in this
-- pipeline.
--
-- pdf_render_status is deliberately separate from render_status: the latter
-- answers "is this version's JSON fit to serve" (AnalysisResult.status),
-- the former "did the PDF itself render". A PDF rendering bug must not hide
-- an otherwise-good JSON report from get_report()'s default filter.
ALTER TABLE dpr_report_artifacts
    ADD COLUMN IF NOT EXISTS pdf_render_status TEXT NOT NULL DEFAULT 'PENDING',
    ADD COLUMN IF NOT EXISTS report_pdf BYTEA;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'dpr_report_artifacts_pdf_render_status_check'
    ) THEN
        ALTER TABLE dpr_report_artifacts
            ADD CONSTRAINT dpr_report_artifacts_pdf_render_status_check
            CHECK (pdf_render_status IN ('PENDING', 'RENDERED', 'FAILED'));
    END IF;
END $$;

-- Pre-007 rows never attempted a CAM-report render; leave them PENDING
-- (report_pdf NULL) rather than claiming RENDERED for a PDF that was never
-- produced -- a format=pdf request for one of these old versions gets a
-- clear "not available" response, never a silently stale or wrong file.

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
            'dpr_report_artifacts rows are immutable except render_status/pdf_render_status/report_pdf (source_reference=%, version=%)',
            OLD.source_reference, OLD.version;
    END IF;

    IF OLD.render_status IS DISTINCT FROM NEW.render_status
        AND (rank->>OLD.render_status)::int >= (rank->>NEW.render_status)::int
    THEN
        RAISE EXCEPTION
            'render_status may only move forward (% -> % is not forward, source_reference=%, version=%)',
            OLD.render_status, NEW.render_status, OLD.source_reference, OLD.version;
    END IF;

    IF OLD.pdf_render_status IS DISTINCT FROM NEW.pdf_render_status
        AND (rank->>OLD.pdf_render_status)::int >= (rank->>NEW.pdf_render_status)::int
    THEN
        RAISE EXCEPTION
            'pdf_render_status may only move forward (% -> % is not forward, source_reference=%, version=%)',
            OLD.pdf_render_status, NEW.pdf_render_status, OLD.source_reference, OLD.version;
    END IF;

    -- report_pdf may only be set in the same update that moves
    -- pdf_render_status off PENDING -- never changed again afterwards.
    IF OLD.report_pdf IS DISTINCT FROM NEW.report_pdf
        AND OLD.pdf_render_status IS NOT DISTINCT FROM NEW.pdf_render_status
    THEN
        RAISE EXCEPTION
            'report_pdf may only be set alongside a pdf_render_status transition (source_reference=%, version=%)',
            OLD.source_reference, OLD.version;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
