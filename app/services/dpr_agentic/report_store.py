"""Durable storage for a completed DPR analysis report.

Persists only the already-validated AnalysisResult JSON that analyze_parallel()
produced -- never raw document bytes, uploaded file content or LLM prompts.
report_access.py decides whether a caller may read it; this module only
stores and retrieves.

Append-only versioning (migration 005): a run_id's reports live in
`dpr_report_artifacts` keyed by (run_id, version), never a single row per
run_id. store_report() only ever INSERTs a new row at the next version
number; no UPDATE statement touches this table anywhere in this module.
A previous report version can therefore never be overwritten or lost
(Section 16) -- even the narrow lease-reclaim race where two workers both
complete the same run_id (see PROGRESS.md milestone 4c) now just produces
two versions instead of a conflict: get_report() returns the latest by
default, and every earlier version stays retrievable by number.
"""

import json

from sqlalchemy import text

from .schemas import AnalysisResult


def store_report(engine, run_id: str, result: AnalysisResult) -> int:
    """Appends a new version of the report for `run_id` and returns the
    version number just stored (1 for the first report, 2 for the next,
    and so on). Never updates or replaces an existing row."""
    payload = json.dumps(result.model_dump(mode="json"))
    with engine.begin() as conn:
        # Lock any existing rows for this run_id before computing the next
        # version number, so two concurrent stores serialize rather than
        # both computing the same number (Postgres disallows FOR UPDATE
        # directly on an aggregate query, hence the two steps).
        existing_versions = (
            conn.execute(
                text(
                    "SELECT version FROM dpr_report_artifacts "
                    "WHERE run_id = :run_id FOR UPDATE"
                ),
                {"run_id": run_id},
            )
            .scalars()
            .all()
        )
        next_version = max(existing_versions, default=0) + 1
        conn.execute(
            text("""
                INSERT INTO dpr_report_artifacts (run_id, version, report)
                VALUES (:run_id, :version, CAST(:report AS jsonb))
            """),
            {"run_id": run_id, "version": next_version, "report": payload},
        )
    return next_version


def get_report(
    engine, run_id: str, *, version: int | None = None
) -> AnalysisResult | None:
    """Returns the report for `run_id` -- the latest stored version by
    default, or a specific one if `version` is given. Every version ever
    stored remains retrievable this way."""
    query = (
        "SELECT report::text AS report FROM dpr_report_artifacts WHERE run_id=:run_id"
    )
    params: dict[str, str | int] = {"run_id": run_id}
    if version is not None:
        query += " AND version=:version"
        params["version"] = version
    else:
        query += " ORDER BY version DESC LIMIT 1"
    with engine.connect() as conn:
        row = conn.execute(text(query), params).scalar_one_or_none()
    return AnalysisResult.model_validate(json.loads(row)) if row else None


def list_report_versions(engine, run_id: str) -> list[int]:
    """All version numbers ever stored for `run_id`, oldest first."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT version FROM dpr_report_artifacts "
                "WHERE run_id=:run_id ORDER BY version"
            ),
            {"run_id": run_id},
        ).scalars()
        return list(rows)
