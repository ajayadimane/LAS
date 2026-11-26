"""Durable Core LAS DPR analysis job registry (PostgreSQL recommended).

A lease is used to stop two workers claiming the same run. Stale jobs are reclaimable.
No Wallet writes or automatic report release occur here.
"""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import create_engine, text


def engine_for(database_url: str):
    return create_engine(database_url, pool_pre_ping=True, future=True)


def _key(
    service_request_id: str,
    source_reference: str,
    document_sha256: str,
    analysis_version: str,
) -> str:
    return (
        f"{service_request_id}:{source_reference}:{document_sha256}:{analysis_version}"
    )


def _insert_run(
    db,
    *,
    service_request_id: str,
    document_sha256: str,
    source_reference: str,
    analysis_version: str,
) -> tuple[str, bool]:
    key = _key(service_request_id, source_reference, document_sha256, analysis_version)
    row = db.execute(
        text("""
        INSERT INTO dpr_analysis_runs (run_id, idempotency_key, service_request_id,
          source_reference, document_sha256, analysis_version, status)
        VALUES (:id, :key, :sr, :ref, :sha, :version, 'QUEUED')
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING run_id
    """),
        {
            "id": str(uuid4()),
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


def submit(
    engine,
    *,
    service_request_id: str,
    document_sha256: str,
    source_reference: str,
    analysis_version: str = "v1",
) -> tuple[str, bool]:
    """Idempotent insert; returns (run_id, created). Payload is NOT stored here."""
    with engine.begin() as db:
        return _insert_run(
            db,
            service_request_id=service_request_id,
            document_sha256=document_sha256,
            source_reference=source_reference,
            analysis_version=analysis_version,
        )


def submit_capped(
    engine,
    *,
    cap_prefix: str,
    since,
    limit: int,
    service_request_id: str,
    document_sha256: str,
    source_reference: str,
    analysis_version: str = "v1",
) -> tuple[str, bool] | None:
    """submit(), unless `limit` runs matching `cap_prefix` (a source_reference
    LIKE pattern, e.g. one customer's every service) were submitted since
    `since`; then None. The count and the insert share one transaction under
    an advisory lock on the prefix, so concurrent uploads for the same
    customer (dashboard + BD + email) cannot all pass a check-then-insert."""
    with engine.begin() as db:
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:prefix))"),
            {"prefix": cap_prefix},
        )
        recent = db.execute(
            text("""
                SELECT COUNT(*) FROM dpr_analysis_runs
                WHERE source_reference LIKE :prefix AND created_at >= :since
            """),
            {"prefix": cap_prefix, "since": since},
        ).scalar_one()
        if recent >= limit:
            return None
        return _insert_run(
            db,
            service_request_id=service_request_id,
            document_sha256=document_sha256,
            source_reference=source_reference,
            analysis_version=analysis_version,
        )


def requeue_failed(engine, run_id: str) -> bool:
    """Puts a FAILED run back in the queue with a fresh attempt budget -- the
    same document uploaded again is the customer asking for a retry (its
    idempotency key maps it to the failed run, so submit() makes no new
    one). created_at moves to now: it is the latest submission again, and
    counts against the daily cap. Only FAILED runs; a COMPLETED one already
    has its report."""
    with engine.begin() as db:
        row = db.execute(
            text("""
            UPDATE dpr_analysis_runs SET status='QUEUED', attempts=0,
              error_code=NULL, artifact_ref=NULL, lease_token=NULL,
              lease_until=NULL, created_at=CURRENT_TIMESTAMP,
              updated_at=CURRENT_TIMESTAMP
            WHERE run_id=:id AND status='FAILED'
            RETURNING run_id
        """),
            {"id": run_id},
        ).scalar_one_or_none()
    return row is not None


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


def release(engine, run_id: str, token: str) -> bool:
    """Hands a run this worker still holds back to the queue, unfinished --
    for a deliberate stop (SIGTERM on deploy), so the run is picked up again
    at once instead of after the lease expires. The interrupted attempt is
    not counted against the attempt cap: it was our stop, not the run's
    failure."""
    try:
        UUID(token)
    except (ValueError, AttributeError, TypeError):
        return False
    with engine.begin() as db:
        row = db.execute(
            text("""
            UPDATE dpr_analysis_runs SET status='QUEUED', lease_token=NULL,
              lease_until=NULL, attempts=GREATEST(attempts - 1, 0),
              updated_at=CURRENT_TIMESTAMP
            WHERE run_id=:id AND lease_token=:token AND status='RUNNING'
            RETURNING run_id
        """),
            {"id": run_id, "token": token},
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
    claimed before raising -- e.g. a config error evaluated while building
    `run_claimed_analysis()`'s own arguments, which raises before `claim()`
    ever runs (see scripts/dpr_worker.py's process_one(), the only caller).
    Without this, such a run stays QUEUED forever and is retried identically
    by every poll cycle, rather than ending up FAILED with a reason recorded.

    Only touches a run nobody holds: QUEUED, or RUNNING with an expired
    lease. A RUNNING run under a live lease belongs to another worker and is
    never failed from here. A no-op once a run is COMPLETED or FAILED.
    """
    with engine.begin() as db:
        row = db.execute(
            text("""
            UPDATE dpr_analysis_runs SET status='FAILED', error_code=:error_code,
              lease_until=NULL, lease_token=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE run_id=:id AND (status='QUEUED'
              OR (status='RUNNING' AND lease_until < CURRENT_TIMESTAMP))
            RETURNING run_id
        """),
            {"id": run_id, "error_code": error_code},
        ).scalar_one_or_none()
        return row is not None


def _merge_by_role(old: dict | None, new: dict | None) -> dict | None:
    """Adds one attempt's per-role usage onto what earlier attempts stored:
    counts summed, cost_usd summed as Decimal strings."""
    if not old:
        return new
    if not new:
        return old
    merged = {role: dict(row) for role, row in old.items()}
    for role, row in new.items():
        into = merged.setdefault(role, {})
        for k, v in row.items():
            if k == "cost_usd":
                into[k] = str(Decimal(str(into.get(k) or 0)) + Decimal(str(v)))
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                into[k] = (into.get(k) or 0) + v
            else:
                into[k] = v
    return merged


def record_token_usage(
    engine,
    run_id: str,
    *,
    gemini_calls: int,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    thinking_tokens: int | None = None,
    cached_tokens: int | None = None,
    search_queries: int | None = None,
    gemini_model: str | None = None,
    cost_usd: Decimal | None = None,
    cost_inr: Decimal | None = None,
    usd_to_inr: float | None = None,
    price_rates: dict | None = None,
    usage_by_role: dict | None = None,
) -> None:
    """Adds one attempt's Gemini usage to a run (migration 008), with what it
    cost (013, run_cost.py; NULL when no rates are configured) and the
    per-role breakdown (016).

    Additive, because a run can take more than one attempt (a stop on
    deploy hands it back to the queue; a lost lease lets another worker
    take it) and every attempt's calls were billed. The first call onto a
    NULL row simply stores the values, so a single-attempt run reads the
    same as before. scripts/dpr_worker.py calls this only for an attempt it
    actually claimed, from its own `finally`; it never checks status or
    lease. Writing 0s for an attempt that made no Gemini calls is correct and
    distinct from a pre-migration row, which stays NULL. A failed run is
    costed too: its calls were billed."""
    with engine.begin() as db:
        old_by_role = db.execute(
            text(
                "SELECT usage_by_role FROM dpr_analysis_runs "
                "WHERE run_id=:id FOR UPDATE"
            ),
            {"id": run_id},
        ).scalar_one_or_none()
        by_role = _merge_by_role(old_by_role, usage_by_role)
        db.execute(
            text("""
            UPDATE dpr_analysis_runs SET
              gemini_calls=COALESCE(gemini_calls, 0) + :calls,
              prompt_tokens=COALESCE(prompt_tokens, 0) + :prompt,
              completion_tokens=COALESCE(completion_tokens, 0) + :completion,
              total_tokens=COALESCE(total_tokens, 0) + :total,
              thinking_tokens=CASE WHEN CAST(:thinking AS BIGINT) IS NULL
                THEN thinking_tokens
                ELSE COALESCE(thinking_tokens, 0) + :thinking END,
              cached_tokens=CASE WHEN CAST(:cached AS BIGINT) IS NULL
                THEN cached_tokens
                ELSE COALESCE(cached_tokens, 0) + :cached END,
              search_queries=CASE WHEN CAST(:searches AS BIGINT) IS NULL
                THEN search_queries
                ELSE COALESCE(search_queries, 0) + :searches END,
              gemini_model=COALESCE(:model, gemini_model),
              cost_usd=CASE WHEN CAST(:cost_usd AS NUMERIC) IS NULL THEN cost_usd
                ELSE COALESCE(cost_usd, 0) + :cost_usd END,
              cost_inr=CASE WHEN CAST(:cost_inr AS NUMERIC) IS NULL THEN cost_inr
                ELSE COALESCE(cost_inr, 0) + :cost_inr END,
              usd_to_inr=COALESCE(:usd_to_inr, usd_to_inr),
              price_rates=COALESCE(CAST(:rates AS JSONB), price_rates),
              usage_by_role=CAST(:by_role AS JSONB),
              updated_at=CURRENT_TIMESTAMP
            WHERE run_id=:id
        """),
            {
                "id": run_id,
                "calls": gemini_calls,
                "prompt": prompt_tokens,
                "completion": completion_tokens,
                "total": total_tokens,
                "thinking": thinking_tokens,
                "cached": cached_tokens,
                "searches": search_queries,
                "model": gemini_model,
                "cost_usd": cost_usd,
                "cost_inr": cost_inr,
                "usd_to_inr": usd_to_inr,
                "rates": json.dumps(price_rates) if price_rates is not None else None,
                "by_role": json.dumps(by_role) if by_role is not None else None,
            },
        )


def get_run(engine, run_id: str) -> dict | None:
    with engine.connect() as db:
        row = (
            db.execute(
                text("""SELECT run_id, service_request_id, source_reference,
            document_sha256, status, attempts, artifact_ref, error_code,
            gemini_calls, prompt_tokens, completion_tokens, total_tokens,
            thinking_tokens, cached_tokens, search_queries, gemini_model,
            cost_usd, cost_inr, created_at, updated_at
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
            gemini_calls, prompt_tokens, completion_tokens, total_tokens,
            thinking_tokens, cached_tokens, search_queries, gemini_model,
            cost_usd, cost_inr, created_at, updated_at
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
    expired lease and attempts still under the cap -- and only once the
    upload is staged. submit() and stage_upload() are separate commits, so a
    worker polling between them would otherwise claim a run with nothing to
    read and fail it. For scripts/dpr_worker.py to discover work -- claim()
    itself needs a run_id already in hand."""
    with engine.connect() as db:
        rows = (
            db.execute(
                text("""
                SELECT r.run_id FROM dpr_analysis_runs r
                WHERE (r.status='QUEUED'
                       OR (r.status='RUNNING' AND r.lease_until < CURRENT_TIMESTAMP))
                  AND r.attempts < 4
                  AND EXISTS (SELECT 1 FROM dpr_upload_staging s
                              WHERE s.run_id = r.run_id)
                ORDER BY r.created_at ASC
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
