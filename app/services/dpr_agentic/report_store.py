"""Durable storage for a completed DPR analysis report.

Persists only the already-validated AnalysisResult JSON that analyze_parallel()
produced -- never raw document bytes, uploaded file content or LLM prompts.
report_access.py decides whether a caller may read it; this module only
stores and retrieves.

Append-only versioning (migration 005), rescoped to the service, not the run
(migration 006): re-analysis after DPR corrections submits a brand-new run_id
(a new document_sha256 makes jobs.submit()'s idempotency_key different), so
versions keyed by run_id would restart at v1 on every correction. Versions
are instead keyed by source_reference -- the same person+service key
report_service.latest_run_for() already uses to span runs -- so a correction
continues the same history as v2, v3, and so on. A previous version is never
updated or deleted (Section 16): the database enforces this (see migration
006's trigger) independently of this module's own discipline of never
issuing an UPDATE/DELETE against dpr_report_artifacts.

Idempotency: storing the same run_id with byte-identical report content
twice (e.g. the lease-reclaim race in PROGRESS.md milestone 4c, now retried
rather than left to race) returns the existing version instead of appending
a redundant one -- enforced by a UNIQUE (run_id, snapshot_sha256) constraint,
not just an application-level check.

get_report() defaults to the latest version with render_status='RENDERED',
so a version whose render never completed is not served as "the" report.
"""

import hashlib
import json

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .schemas import AnalysisResult

_MAX_VERSION_ATTEMPTS = 5


def _canonical_json(result: AnalysisResult) -> str:
    return json.dumps(result.model_dump(mode="json"), sort_keys=True)


def store_report(engine, run_id: str, result: AnalysisResult) -> int:
    """Appends a new version of the report for `run_id`'s service and
    returns its version number (1 for the first report for that service, 2
    for the next, and so on) -- or, if this exact run_id already stored this
    exact report content, the version it was already given.

    Never updates or replaces an existing row. Retries a bounded number of
    times on a concurrent-writer conflict: two stores for the same service
    with no prior version can both compute "next version = 1" before either
    commits, so the UNIQUE (source_reference, version) constraint is the
    real arbiter, not the SELECT ... FOR UPDATE below (which only serializes
    writers once at least one row already exists to lock).
    """
    payload = _canonical_json(result)
    snapshot_sha256 = hashlib.sha256(payload.encode()).hexdigest()
    render_status = "RENDERED" if result.status == "completed" else "PENDING"

    for _ in range(_MAX_VERSION_ATTEMPTS):
        try:
            with engine.begin() as conn:
                source_reference = conn.execute(
                    text(
                        "SELECT source_reference FROM dpr_analysis_runs "
                        "WHERE run_id=:id"
                    ),
                    {"id": run_id},
                ).scalar_one()

                existing = conn.execute(
                    text(
                        "SELECT version FROM dpr_report_artifacts "
                        "WHERE run_id=:run_id AND snapshot_sha256=:sha"
                    ),
                    {"run_id": run_id, "sha": snapshot_sha256},
                ).scalar_one_or_none()
                if existing is not None:
                    return existing

                locked_versions = (
                    conn.execute(
                        text(
                            "SELECT version FROM dpr_report_artifacts "
                            "WHERE source_reference = :ref FOR UPDATE"
                        ),
                        {"ref": source_reference},
                    )
                    .scalars()
                    .all()
                )
                next_version = max(locked_versions, default=0) + 1
                conn.execute(
                    text("""
                        INSERT INTO dpr_report_artifacts
                            (run_id, source_reference, version, snapshot_sha256,
                             render_status, report)
                        VALUES
                            (:run_id, :ref, :version, :sha, :render_status,
                             CAST(:report AS jsonb))
                    """),
                    {
                        "run_id": run_id,
                        "ref": source_reference,
                        "version": next_version,
                        "sha": snapshot_sha256,
                        "render_status": render_status,
                        "report": payload,
                    },
                )
            return next_version
        except IntegrityError:
            # Another writer took this version number (or stored this same
            # run_id's content first) between our SELECT and INSERT; retry
            # from scratch so either branch above resolves against the now-
            # committed row.
            continue

    raise RuntimeError(
        f"store_report: exceeded {_MAX_VERSION_ATTEMPTS} attempts "
        f"racing concurrent writers for run_id={run_id!r}"
    )


def get_report(
    engine, source_reference: str, *, version: int | None = None
) -> AnalysisResult | None:
    """Returns the report for `source_reference` -- by default the latest
    version with render_status='RENDERED', or a specific version (regardless
    of render_status) if `version` is given."""
    query = (
        "SELECT report::text AS report FROM dpr_report_artifacts "
        "WHERE source_reference = :ref"
    )
    params: dict[str, str | int] = {"ref": source_reference}
    if version is not None:
        query += " AND version = :version"
        params["version"] = version
    else:
        query += " AND render_status = 'RENDERED' ORDER BY version DESC LIMIT 1"
    with engine.connect() as conn:
        row = conn.execute(text(query), params).scalar_one_or_none()
    return AnalysisResult.model_validate(json.loads(row)) if row else None


def list_report_versions(engine, source_reference: str) -> list[int]:
    """All version numbers ever stored for `source_reference`, oldest first."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT version FROM dpr_report_artifacts "
                "WHERE source_reference = :ref ORDER BY version"
            ),
            {"ref": source_reference},
        ).scalars()
        return list(rows)
