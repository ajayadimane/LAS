"""Durable Core LAS DPR analysis job registry (PostgreSQL recommended).

A lease is used to stop two workers claiming the same run. Stale jobs are reclaimable.
No Wallet writes or automatic report release occur here.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import create_engine, text


def engine_for(database_url: str):
    return create_engine(database_url, pool_pre_ping=True, future=True)


def submit(
    engine,
    *,
    service_request_id: str,
    document_sha256: str,
    source_reference: str,
    analysis_version: str = "v1",
) -> tuple[str, bool]:
    """Idempotent insert; returns (run_id, created). Payload is NOT stored here."""
    run_id = str(uuid4())
    key = (
        f"{service_request_id}:{source_reference}:{document_sha256}:{analysis_version}"
    )
    with engine.begin() as db:
        row = db.execute(
            text("""
            INSERT INTO dpr_analysis_runs (run_id, idempotency_key, service_request_id,
              source_reference, document_sha256, analysis_version, status)
            VALUES (:id, :key, :sr, :ref, :sha, :version, 'QUEUED')
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING run_id
        """),
            {
                "id": run_id,
                "key": key,
                "sr": service_request_id,
                "ref": source_reference,
                "sha": document_sha256,
                "version": analysis_version,
            },
        ).scalar_one_or_none()
        if row:
            return str(row), True
        existing = db.execute(
            text("SELECT run_id FROM dpr_analysis_runs WHERE idempotency_key=:key"),
            {"key": key},
        ).scalar_one()
        return str(existing), False


def claim(engine, run_id: str, *, lease_seconds: int = 900) -> str | None:
    token = str(uuid4())
    with engine.begin() as db:
        claimed = db.execute(
            text("""
            UPDATE dpr_analysis_runs SET status='RUNNING', lease_token=:token,
              lease_until=CURRENT_TIMESTAMP + (:lease_seconds * interval '1 second'),
              attempts=attempts+1, updated_at=CURRENT_TIMESTAMP
            WHERE run_id=:id AND (status='QUEUED' OR
              (status='RUNNING' AND lease_until < CURRENT_TIMESTAMP))
              AND attempts < 4
            RETURNING run_id
        """),
            {"id": run_id, "token": token, "lease_seconds": lease_seconds},
        ).scalar_one_or_none()
    return token if claimed else None


def renew_lease(engine, run_id: str, token: str, *, lease_seconds: int) -> bool:
    """Extends the lease on a run this worker still holds and is still
    actively processing -- for an analysis that runs longer than its
    original lease (see worker.run_claimed_analysis's heartbeat_interval).
    Never touches status, only how long until claim() may reclaim it."""
    try:
        UUID(token)
    except (ValueError, AttributeError, TypeError):
        return False
    with engine.begin() as db:
        row = db.execute(
            text("""
            UPDATE dpr_analysis_runs
            SET lease_until=CURRENT_TIMESTAMP + (:lease_seconds * interval '1 second'),
                updated_at=CURRENT_TIMESTAMP
            WHERE run_id=:id AND lease_token=:token AND status='RUNNING'
            RETURNING run_id
        """),
            {"id": run_id, "token": token, "lease_seconds": lease_seconds},
        ).scalar_one_or_none()
    return row is not None


def finish(
    engine,
    run_id: str,
    token: str,
    *,
    success: bool,
    artifact_ref: str | None = None,
    error_code: str | None = None,
) -> bool:
    try:
        UUID(token)
    except (ValueError, AttributeError, TypeError):
        # lease_token is a UUID column; a malformed token can never be a
        # match, so this refuses it the same way a wrong-but-valid one does
        # rather than letting the DB raise a raw type error.
        return False
    with engine.begin() as db:
        row = db.execute(
            text("""
            UPDATE dpr_analysis_runs SET status=:status, artifact_ref=:artifact_ref,
              error_code=:error_code, lease_until=NULL, lease_token=NULL,
              updated_at=CURRENT_TIMESTAMP
            WHERE run_id=:id AND lease_token=:token AND status='RUNNING'
            RETURNING run_id
        """),
            {
                "status": "COMPLETED" if success else "FAILED",
                "artifact_ref": artifact_ref,
                "error_code": error_code,
                "id": run_id,
                "token": token,
            },
        ).scalar_one_or_none()
        return row is not None


def fail_stuck_run(engine, run_id: str, error_code: str) -> bool:
    """Terminal-fails a run that `finish()` cannot reach because it was never
    claimed, or lost its lease, before raising -- e.g. a config error
    evaluated while building `run_claimed_analysis()`'s own arguments, which
    raises before `claim()` ever runs (see scripts/dpr_worker.py's
    process_one(), the only caller). Without this, such a run stays QUEUED
    forever and is retried identically by every poll cycle, rather than
    ending up FAILED with a reason recorded.

    Deliberately ignores the lease token (there may be none to check): this
    is a last-resort fallback the caller only reaches after its own
    try/except around the whole claim-to-finish sequence, and it is a no-op
    once a run is already COMPLETED or FAILED, so calling it after a normal
    finish() (which already succeeded) is always safe.
    """
    with engine.begin() as db:
        row = db.execute(
            text("""
            UPDATE dpr_analysis_runs SET status='FAILED', error_code=:error_code,
              lease_until=NULL, lease_token=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE run_id=:id AND status IN ('QUEUED','RUNNING')
            RETURNING run_id
        """),
            {"id": run_id, "error_code": error_code},
        ).scalar_one_or_none()
        return row is not None


def get_run(engine, run_id: str) -> dict | None:
    with engine.connect() as db:
        row = (
            db.execute(
                text("""SELECT run_id, service_request_id, source_reference,
            document_sha256, status, attempts, artifact_ref, error_code,
            created_at, updated_at
            FROM dpr_analysis_runs WHERE run_id=:id"""),
                {"id": run_id},
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None


def latest_run_for(engine, source_reference: str) -> dict | None:
    """The most recently submitted run for one source (e.g. a person+service).

    Several runs can share a source_reference -- one per uploaded document --
    so only the latest is ever report-eligible; see report_service.py.
    """
    with engine.connect() as db:
        row = (
            db.execute(
                text("""SELECT run_id, service_request_id, source_reference,
            document_sha256, status, attempts, artifact_ref, error_code,
            created_at, updated_at
            FROM dpr_analysis_runs WHERE source_reference=:ref
            ORDER BY created_at DESC LIMIT 1"""),
                {"ref": source_reference},
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None


def list_claimable(engine, limit: int = 10) -> list[str]:
    """Run ids claim() could currently take: QUEUED, or RUNNING with an
    expired lease and attempts still under the cap. For scripts/dpr_worker.py
    to discover work -- claim() itself needs a run_id already in hand."""
    with engine.connect() as db:
        rows = (
            db.execute(
                text("""
                SELECT run_id FROM dpr_analysis_runs
                WHERE (status='QUEUED'
                       OR (status='RUNNING' AND lease_until < CURRENT_TIMESTAMP))
                  AND attempts < 4
                ORDER BY created_at ASC
                LIMIT :limit
            """),
                {"limit": limit},
            )
            .scalars()
            .all()
        )
        return [str(r) for r in rows]


def count_recent_submissions(engine, source_reference_prefix: str, since) -> int:
    """How many runs matching this source_reference prefix (e.g. every
    service for one customer: "wallet-person-custom-field:<id>:%") were
    submitted since `since` -- backs the per-customer daily cap in
    app/api/v1/customer.py, so repeated uploads can't drive unbounded Gemini
    spend. A plain LIKE scan is fine at this scale; add an index on
    (source_reference, created_at) if submission volume grows."""
    with engine.connect() as db:
        return db.execute(
            text("""
                SELECT COUNT(*) FROM dpr_analysis_runs
                WHERE source_reference LIKE :prefix AND created_at >= :since
            """),
            {"prefix": source_reference_prefix, "since": since},
        ).scalar_one()


def reap_exhausted(engine) -> list[str]:
    """Runs claim() will never reclaim again (lease expired, attempts used
    up) -- mark them FAILED so they don't sit RUNNING forever, and return
    their ids so the caller can clean up anything staged for them (see
    upload_staging.py). This is the only place attempts >= 4 ever reaches a
    terminal status."""
    with engine.begin() as db:
        rows = (
            db.execute(
                text("""
                UPDATE dpr_analysis_runs
                SET status='FAILED', error_code='ATTEMPTS_EXHAUSTED',
                    lease_until=NULL, lease_token=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE status='RUNNING' AND lease_until < CURRENT_TIMESTAMP
                  AND attempts >= 4
                RETURNING run_id
            """)
            )
            .scalars()
            .all()
        )
        return [str(r) for r in rows]
