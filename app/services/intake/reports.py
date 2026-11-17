"""Bookkeeping for writing finished DPR reports back to the wallet
(migrations/014_dpr_report_wallet_sync.sql).

The DPR worker stores reports in our Postgres and never calls the vendor (it
holds no token). Core's intake worker, which has its own vendor session,
picks up each newly rendered report version once: the service status becomes
"Report ready", the PDF goes to the service's report custom field,
and the BD is emailed (app/services/intake/processor.py:_sync_reports).

Synchronous SQLAlchemy, run through asyncio.to_thread by the processor.
"""

from dataclasses import dataclass

from sqlalchemy import text

SOURCE_PREFIX = "wallet-person-custom-field:"
MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class ReportToSync:
    run_id: str
    source_reference: str
    version: int

    @property
    def person_and_code(self) -> tuple[str, str] | None:
        """wallet-person-custom-field:<person_id>:<code> -> (person_id, code)."""
        if not self.source_reference.startswith(SOURCE_PREFIX):
            return None
        rest = self.source_reference[len(SOURCE_PREFIX) :]
        person_id, _, code = rest.rpartition(":")
        return (person_id, code) if person_id and code else None


def discover(engine, lookback_hours: float) -> int:
    """Queue every report version rendered recently and not yet synced. The
    PDF may have failed to render: the status is still written then."""
    with engine.begin() as conn:
        return conn.execute(
            text("""
                INSERT INTO dpr_report_wallet_sync (run_id, source_reference, version)
                SELECT a.run_id, a.source_reference, a.version
                FROM dpr_report_artifacts a
                WHERE a.render_status = 'RENDERED'
                  AND a.pdf_render_status IN ('RENDERED', 'FAILED')
                  AND a.source_reference LIKE :prefix
                  AND a.created_at > now() - make_interval(secs => :lookback)
                ON CONFLICT (run_id) DO NOTHING
            """),
            {"prefix": SOURCE_PREFIX + "%", "lookback": lookback_hours * 3600},
        ).rowcount


def claim_due(engine, limit: int = 10) -> list[ReportToSync]:
    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                UPDATE dpr_report_wallet_sync SET attempts = attempts + 1,
                    next_attempt_at =
                        now() + make_interval(secs => 60 * power(2, attempts)),
                    updated_at = now()
                WHERE run_id IN (
                    SELECT run_id FROM dpr_report_wallet_sync
                    WHERE status IN ('PENDING', 'FAILED') AND attempts < :max
                      AND next_attempt_at <= now()
                    ORDER BY created_at LIMIT :limit
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING run_id, source_reference, version
            """),
            {"max": MAX_ATTEMPTS, "limit": limit},
        ).all()
    return [ReportToSync(str(r[0]), r[1], r[2]) for r in rows]


def finish(
    engine,
    run_id: str,
    status: str,
    *,
    outcome: str | None = None,
    bd_emailed: bool = False,
    error: str | None = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE dpr_report_wallet_sync SET status = :status, outcome = :outcome,
                    bd_emailed = :emailed, last_error = :error, updated_at = now()
                WHERE run_id = :id
            """),
            {
                "id": run_id,
                "status": status,
                "outcome": outcome,
                "emailed": bd_emailed,
                "error": (error or "")[:500] or None,
            },
        )


def latest_case_for_person(engine, person_id: str) -> str | None:
    """The newest intake case of this customer (for threading the BD mail)."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT case_id FROM intake_cases WHERE person_id = :p "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"p": person_id},
        ).first()
    return str(row[0]) if row else None
