-- Apply to Core LAS-owned PostgreSQL only, after migrations 001 and 002.
-- Holds the already-generated AnalysisResult JSON for a completed run, never
-- raw document bytes. report_access.py decides whether a caller may read a
-- row here; this table only persists what analyze_parallel() produced.
CREATE TABLE IF NOT EXISTS dpr_report_artifacts (
    run_id UUID PRIMARY KEY REFERENCES dpr_analysis_runs(run_id) ON DELETE CASCADE,
    report JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
