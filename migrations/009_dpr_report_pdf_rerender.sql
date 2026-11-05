-- Apply to Core LAS-owned PostgreSQL only, after migrations 001-008.
--
-- Lets a CAM PDF that failed to render be rendered again later
-- (report_store.rerender_failed_pdfs(), scripts/rerender_cam_pdfs.py).
--
-- Why: a render can fail for reasons unrelated to the report itself -- the
-- first real run (2026-11-05, 55 findings) hit the worker's render timeout
-- because of a template layout cost, since fixed in base.css. Under 007's
-- rule FAILED was final, so that version could never get a PDF without
-- re-running (and re-paying for) the whole Gemini analysis.
--
-- Exactly one transition is added: pdf_render_status FAILED -> RENDERED, and
-- only together with non-null report_pdf bytes. Everything else from 007 is
-- unchanged: the report JSON, sha, version, run and created_at stay
-- immutable; DELETE is refused; render_status still only moves forward; a
-- RENDERED PDF can never be replaced or un-rendered. The PDF is a pure
-- rendering of the stored, immutable report JSON, so rendering it again
-- does not change what the report says.

CREATE OR REPLACE FUNCTION dpr_report_artifacts_immutable() RETURNS trigger AS $$
DECLARE
    rank jsonb := '{"PENDING": 0, "RENDERED": 1, "FAILED": 1}'::jsonb;
    pdf_retry boolean;
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

    -- New in 009: a failed PDF may be rendered again, once, with its bytes.
    pdf_retry := OLD.pdf_render_status = 'FAILED'
        AND NEW.pdf_render_status = 'RENDERED'
        AND NEW.report_pdf IS NOT NULL;

    IF OLD.pdf_render_status IS DISTINCT FROM NEW.pdf_render_status
        AND NOT pdf_retry
        AND (rank->>OLD.pdf_render_status)::int >= (rank->>NEW.pdf_render_status)::int
    THEN
        RAISE EXCEPTION
            'pdf_render_status may only move forward (% -> % is not forward, source_reference=%, version=%)',
            OLD.pdf_render_status, NEW.pdf_render_status, OLD.source_reference, OLD.version;
    END IF;

    -- report_pdf may only be set in the same update that moves
    -- pdf_render_status off PENDING (or FAILED -> RENDERED, above) --
    -- never changed again afterwards.
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
