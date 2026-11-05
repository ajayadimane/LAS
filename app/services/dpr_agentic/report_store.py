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

Each version also gets a one-time CAM six-page PDF render (cam_render.py),
attempted once at store time and never regenerated afterwards -- that's what
lets GET .../report?format=pdf serve stored bytes with no re-rendering on
GET. Its outcome lives in a *separate* `pdf_render_status`/`report_pdf`,
deliberately not folded into `render_status`: a PDF-rendering bug must never
hide an otherwise-good JSON report from get_report()'s default filter. The
render itself runs in a spawned subprocess with a hard wall-clock timeout
(default 120s, `Settings.dpr_cam_pdf_render_timeout_seconds`): WeasyPrint/
cairo/pango have been observed to hang rather than raise (see milestone 5's
concurrent-writer test), and a hang in-process would wedge the single-
threaded worker (scripts/dpr_worker.py) forever. A timeout or any other
render failure becomes `pdf_render_status='FAILED'` -- never a stuck process
-- and never blocks the JSON report (the real source of truth) from being
stored.

Known limitation: `report_pdf` stores the PDF bytes directly in Postgres
(BYTEA). Core LAS has no approved object-storage interface today (see
app/services/service_fields.py's own documented TODO for the same gap on
uploaded documents) -- if one is added, this column should hold a storage
key and checksum instead of the bytes themselves, the same migration.
"""

import hashlib
import json
import multiprocessing

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.logging import get_logger

from .schemas import AnalysisResult

logger = get_logger(__name__)

_MAX_VERSION_ATTEMPTS = 5
DEFAULT_PDF_RENDER_TIMEOUT_SECONDS = 120.0


def _canonical_json(result: AnalysisResult) -> str:
    return json.dumps(result.model_dump(mode="json"), sort_keys=True)


def _render_cam_pdf_worker(
    result_json: str, report_id: str, report_version: int, analysis_run_id: str, conn
) -> None:
    """Entry point for the spawned subprocess (see _render_cam_pdf). Imports
    are local so the parent process never needs WeasyPrint's native deps
    just to define this function, and so a spawned child (which re-imports
    this module fresh, not forking the parent's state) only pays that cost
    when it actually renders."""
    try:
        from .cam_adapter import build_snapshot_from_analysis
        from .cam_render import render_cam_report_pdf

        result = AnalysisResult.model_validate_json(result_json)
        snapshot = build_snapshot_from_analysis(
            result,
            report_id=report_id,
            report_version=report_version,
            analysis_run_id=analysis_run_id,
        )
        conn.send(("ok", render_cam_report_pdf(snapshot)))
    except Exception as exc:  # noqa: BLE001 - reported to the parent, not raised
        conn.send(("error", repr(exc)))
    finally:
        conn.close()


def _run_in_subprocess_with_timeout(
    target, args: tuple, *, timeout_seconds: float
) -> tuple[str, object]:
    """Runs `target(*args, conn)` in a spawned subprocess (never forked --
    forking would duplicate whatever live DB connection/transaction the
    caller holds into the child, which never touches it but has no business
    inheriting it either). `target` must be a module-level function (spawn
    pickles it by module+qualname and re-imports it in the child, so a
    closure or nested function cannot be used).

    Returns ("ok", value), ("error", repr(exc)), or ("timeout", None) --
    and guarantees the subprocess is not left running in any of those
    cases, including the timeout one (terminated, then killed if that
    doesn't land within 5s)."""
    ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=target, args=(*args, child_conn), daemon=True)
    proc.start()
    child_conn.close()
    try:
        if not parent_conn.poll(timeout_seconds):
            proc.terminate()
            proc.join(timeout=5)
            if proc.is_alive():
                proc.kill()
                proc.join()
            return "timeout", None

        try:
            status, payload = parent_conn.recv()
        except EOFError:
            status = "error"
            payload = "child process closed the pipe without a result"
        proc.join(timeout=5)
        if proc.is_alive():
            proc.kill()
            proc.join()
        return status, payload
    finally:
        parent_conn.close()


def _render_cam_pdf(
    result: AnalysisResult,
    *,
    run_id: str,
    source_reference: str,
    version: int,
    timeout_seconds: float = DEFAULT_PDF_RENDER_TIMEOUT_SECONDS,
) -> bytes | None:
    """Renders in a spawned subprocess with a hard timeout; a hang or crash
    there can never stall the caller. Best-effort either way: a PDF-
    rendering failure must never stop the JSON report from being stored.
    No vendor-sourced borrower/project profile is available here --
    store_report() is called from scripts/dpr_worker.py, which never calls
    the vendor (see its module docstring) -- so this renders with the
    templates' own explicit missing-data state for those fields, not
    fabricated values."""
    status, payload = _run_in_subprocess_with_timeout(
        _render_cam_pdf_worker,
        (result.model_dump_json(), f"{source_reference}-v{version}", version, run_id),
        timeout_seconds=timeout_seconds,
    )
    if status == "ok":
        return payload
    if status == "timeout":
        logger.error(
            "dpr_cam_pdf_render_timeout run_id=%s source_reference=%s "
            "version=%d timeout_seconds=%s",
            run_id,
            source_reference,
            version,
            timeout_seconds,
        )
        return None
    logger.error(
        "dpr_cam_pdf_render_failed run_id=%s source_reference=%s version=%d error=%s",
        run_id,
        source_reference,
        version,
        payload,
    )
    return None


def store_report(
    engine,
    run_id: str,
    result: AnalysisResult,
    *,
    pdf_render_timeout_seconds: float = DEFAULT_PDF_RENDER_TIMEOUT_SECONDS,
) -> int:
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

    The PDF render happens before any transaction is opened for the actual
    insert -- it can take up to `pdf_render_timeout_seconds`, and a
    multi-minute-long open Postgres transaction holding `FOR UPDATE` row
    locks would be its own problem independent of the render itself.
    """
    payload = _canonical_json(result)
    snapshot_sha256 = hashlib.sha256(payload.encode()).hexdigest()
    render_status = "RENDERED" if result.status == "completed" else "PENDING"

    with engine.connect() as conn:
        source_reference = conn.execute(
            text("SELECT source_reference FROM dpr_analysis_runs WHERE run_id=:id"),
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
        max_existing_version = conn.execute(
            text(
                "SELECT max(version) FROM dpr_report_artifacts "
                "WHERE source_reference=:ref"
            ),
            {"ref": source_reference},
        ).scalar_one()

    # An unlocked best guess at the version this will become, used only as
    # a label inside the rendered PDF's own content -- nothing in the six
    # pages depends on it being correct, only present. It is right except
    # in the rare case a concurrent writer also takes this same version
    # number below and this call has to retry onto the next one instead;
    # the stored `version` column (what every route actually reads) is
    # always correct regardless, since it comes from the real insert.
    estimated_version = (max_existing_version or 0) + 1
    pdf = _render_cam_pdf(
        result,
        run_id=run_id,
        source_reference=source_reference,
        version=estimated_version,
        timeout_seconds=pdf_render_timeout_seconds,
    )
    pdf_render_status = "RENDERED" if pdf is not None else "FAILED"

    for _ in range(_MAX_VERSION_ATTEMPTS):
        try:
            with engine.begin() as conn:
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
                             render_status, report, pdf_render_status, report_pdf)
                        VALUES
                            (:run_id, :ref, :version, :sha, :render_status,
                             CAST(:report AS jsonb), :pdf_render_status, :pdf)
                    """),
                    {
                        "run_id": run_id,
                        "ref": source_reference,
                        "version": next_version,
                        "sha": snapshot_sha256,
                        "render_status": render_status,
                        "report": payload,
                        "pdf_render_status": pdf_render_status,
                        "pdf": pdf,
                    },
                )
            return next_version
        except IntegrityError:
            # Another writer took this version number (or stored this same
            # run_id's content first) between our SELECT and INSERT; retry
            # from scratch so either branch above resolves against the now-
            # committed row. The PDF (already rendered, keyed only by
            # content, not the version number) is reused as-is.
            continue

    raise RuntimeError(
        f"store_report: exceeded {_MAX_VERSION_ATTEMPTS} attempts "
        f"racing concurrent writers for run_id={run_id!r}"
    )


def rerender_failed_pdfs(
    engine,
    *,
    run_id: str | None = None,
    timeout_seconds: float = DEFAULT_PDF_RENDER_TIMEOUT_SECONDS,
) -> list[dict]:
    """Renders again every stored version whose CAM PDF failed
    (pdf_render_status='FAILED'), or only `run_id`'s. Uses the stored,
    immutable report JSON -- no Gemini call, the analysis is not re-run.

    Allowed by migration 009 (FAILED -> RENDERED, with the bytes, once). A
    render that fails again leaves the row FAILED, untouched. Returns one
    {run_id, source_reference, version, status} per row tried, status
    "RENDERED" or "FAILED"."""
    query = (
        "SELECT id, run_id::text, source_reference, version, report::text "
        "FROM dpr_report_artifacts WHERE pdf_render_status = 'FAILED'"
    )
    params: dict[str, str] = {}
    if run_id is not None:
        query += " AND run_id = CAST(:run_id AS uuid)"
        params["run_id"] = run_id
    query += " ORDER BY id"
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).all()

    outcomes = []
    for row_id, row_run_id, source_reference, version, report_json in rows:
        result = AnalysisResult.model_validate_json(report_json)
        pdf = _render_cam_pdf(
            result,
            run_id=row_run_id,
            source_reference=source_reference,
            version=version,
            timeout_seconds=timeout_seconds,
        )
        status = "FAILED"
        if pdf is not None:
            with engine.begin() as conn:
                updated = conn.execute(
                    text(
                        "UPDATE dpr_report_artifacts "
                        "SET pdf_render_status = 'RENDERED', report_pdf = :pdf "
                        "WHERE id = :id AND pdf_render_status = 'FAILED'"
                    ),
                    {"id": row_id, "pdf": pdf},
                ).rowcount
            # 0 rows: someone else re-rendered it meanwhile -- also fine.
            status = "RENDERED" if updated else "ALREADY_RENDERED"
        logger.info(
            "dpr_cam_pdf_rerender run_id=%s source_reference=%s version=%d status=%s",
            row_run_id,
            source_reference,
            version,
            status,
        )
        outcomes.append(
            {
                "run_id": row_run_id,
                "source_reference": source_reference,
                "version": version,
                "status": status,
            }
        )
    return outcomes


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


def get_report_pdf(
    engine, source_reference: str, *, version: int | None = None
) -> bytes | None:
    """The pre-rendered CAM PDF for `source_reference` -- by default the
    latest version with render_status='RENDERED' (same default as
    get_report()), or a specific version if given. Returns None if that
    version's PDF never rendered (pdf_render_status != 'RENDERED') or the
    version does not exist -- callers must not fall back to rendering
    inline; see this module's docstring."""
    query = (
        "SELECT report_pdf FROM dpr_report_artifacts "
        "WHERE source_reference = :ref AND pdf_render_status = 'RENDERED'"
    )
    params: dict[str, str | int] = {"ref": source_reference}
    if version is not None:
        query += " AND version = :version"
        params["version"] = version
    else:
        query += " AND render_status = 'RENDERED' ORDER BY version DESC LIMIT 1"
    with engine.connect() as conn:
        return conn.execute(text(query), params).scalar_one_or_none()


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
