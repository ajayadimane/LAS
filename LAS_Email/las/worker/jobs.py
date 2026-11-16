"""Periodic jobs (Celery Beat, las/worker/celery_app.py): re-drive stuck mail, refresh the AI
usage rollup, verify the audit chain. Delivery to Core LAS is las/core_handoff.py."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from las.common.audit import verify_chain
from las.common.deps import Deps
from las.common.logging import get_logger
from las.common.models import InboundEmail
from las.common.timeutil import utcnow

log = get_logger(__name__)


async def stale_inbound_ids(deps: Deps, older_than_seconds: int = 120) -> list[int]:
    """Mail persisted but not finished (a worker crashed, or the queue lost the task)."""
    async with deps.db() as s:
        return list(
            (
                await s.execute(
                    select(InboundEmail.inbound_id)
                    .where(
                        InboundEmail.processing_status.in_(("RECEIVED", "PARSED")),
                        InboundEmail.created_at
                        < utcnow() - dt.timedelta(seconds=older_than_seconds),
                    )
                    .limit(200)
                )
            ).scalars()
        )


async def refresh_usage_rollup(deps: Deps) -> None:
    async with deps.db() as s:
        conn = await s.connection()
        await conn.exec_driver_sql("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_ai_usage_daily")
        await s.commit()


async def verify_audit_chain(deps: Deps) -> bool:
    async with deps.db() as s:
        report = await verify_chain(s)
        await s.rollback()
    if not report.ok:
        # This service sends no mail; alerts go to the logs (ops monitor them).
        log.error(
            "audit_chain_verification_failed",
            first_bad_audit_id=report.first_bad_audit_id,
            reason=report.reason,
            rows_checked=report.rows_checked,
        )
    return report.ok
