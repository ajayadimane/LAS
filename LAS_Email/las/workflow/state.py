"""The inbound mail state machine. Every transition writes audit_log in the caller's txn.

Illegal transitions raise ``IllegalTransition``.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from las.common.audit import write_audit
from las.common.models import InboundEmail
from las.common.timeutil import utcnow

INBOUND_TRANSITIONS: dict[str, frozenset[str]] = {
    "RECEIVED": frozenset({"PARSED", "PROCESSED", "REJECTED", "NEEDS_REVIEW", "FAILED", "IGNORED"}),
    "PARSED": frozenset({"PROCESSED", "NEEDS_REVIEW", "FAILED", "REJECTED"}),
    "FAILED": frozenset({"RECEIVED", "PARSED", "PROCESSED", "NEEDS_REVIEW", "REJECTED"}),
    "NEEDS_REVIEW": frozenset({"PROCESSED", "REJECTED"}),
    "PROCESSED": frozenset(),
    "REJECTED": frozenset(),
    "IGNORED": frozenset(),
}


class IllegalTransition(Exception):
    def __init__(self, entity: str, ref: str, from_status: str, to_status: str) -> None:
        super().__init__(f"{entity} {ref}: illegal transition {from_status} -> {to_status}")
        self.from_status = from_status
        self.to_status = to_status


async def transition_inbound(
    session: AsyncSession,
    inbound: InboundEmail,
    to_status: str,
    *,
    error_detail: str | None = None,
) -> None:
    from_status = inbound.processing_status
    if from_status == to_status:
        return
    if to_status not in INBOUND_TRANSITIONS.get(from_status, frozenset()):
        raise IllegalTransition("inbound", inbound.message_id, from_status, to_status)
    inbound.processing_status = to_status
    if error_detail is not None:
        inbound.error_detail = error_detail[:2000]
    if to_status in {"PROCESSED", "REJECTED", "NEEDS_REVIEW", "IGNORED"}:
        inbound.processed_at = utcnow()
    await session.flush()
    await write_audit(
        session,
        actor_type="SYSTEM",
        action="INBOUND_TRANSITION",
        entity_type="inbound_email",
        entity_id=inbound.inbound_id,
        before={"status": from_status},
        after={"status": to_status},
    )
