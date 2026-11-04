"""Durable storage for a completed DPR analysis report.

Persists only the already-validated AnalysisResult JSON that analyze_parallel()
produced -- never raw document bytes, uploaded file content or LLM prompts.
report_access.py decides whether a caller may read it; this module only
stores and retrieves.
"""

import json

from sqlalchemy import text

from .schemas import AnalysisResult


class ReportAlreadyStoredError(RuntimeError):
    """Raised when store_report() is called a second time for a run_id that
    already has a stored report.

    A stored report is immutable: overwriting it would silently destroy a
    previous report version, which the brief explicitly forbids (Section
    16). In the normal job lifecycle this should be unreachable --
    jobs.claim() only lets a worker claim a QUEUED run or a RUNNING one
    whose lease expired, and a COMPLETED run can't be reclaimed -- so this
    only fires on an anomalous double-store (e.g. two workers racing on
    the same claim, or a direct report_store.py call outside the worker
    path). A genuinely new version of the report (the borrower corrected
    and re-uploaded their DPR) gets its own run_id already, via
    jobs.submit()'s content-hash idempotency key -- it was never going to
    collide here.
    """


def store_report(engine, run_id: str, result: AnalysisResult) -> str:
    """Stores the report for `run_id`. Returns `run_id` as the artifact
    reference, since the report is keyed one-to-one with its run. Raises
    ReportAlreadyStoredError rather than overwriting if a report already
    exists for this run_id -- never silently replaces one."""
    payload = json.dumps(result.model_dump(mode="json"))
    with engine.begin() as conn:
        row = conn.execute(
            text("""
                INSERT INTO dpr_report_artifacts (run_id, report)
                VALUES (:run_id, CAST(:report AS jsonb))
                ON CONFLICT (run_id) DO NOTHING
                RETURNING run_id
            """),
            {"run_id": run_id, "report": payload},
        ).scalar_one_or_none()
    if row is None:
        raise ReportAlreadyStoredError(
            f"A report already exists for run_id={run_id!r}; reports are "
            f"immutable once stored."
        )
    return run_id


def get_report(engine, run_id: str) -> AnalysisResult | None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT report::text AS report FROM dpr_report_artifacts "
                "WHERE run_id=:run_id"
            ),
            {"run_id": run_id},
        ).scalar_one_or_none()
    return AnalysisResult.model_validate(json.loads(row)) if row else None
