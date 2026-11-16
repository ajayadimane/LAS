"""Append-only, hash-chained audit log.

``row_hash = sha256(prev_hash || canonical_json(record))``. The writer serialises on
``pg_advisory_xact_lock(hashtext('audit_log'))`` *inside the caller's transaction* before reading the
latest ``row_hash``, so two concurrent writers can never both chain onto the same predecessor. The
lock is released at commit/rollback, which is also when the new row becomes visible.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from las.common.db import advisory_xact_lock
from las.common.models import AuditLog

AUDIT_LOCK_KEY = "audit_log"
GENESIS = "0" * 64


def _json_default(o: Any) -> Any:
    if isinstance(o, dt.datetime | dt.date):
        return o.isoformat()
    return str(o)


def _normalise(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """Round-trip through JSON so what we hash is exactly what jsonb stores and returns."""
    if state is None:
        return None
    result: dict[str, Any] = json.loads(json.dumps(state, default=_json_default, sort_keys=True))
    return result


def _ts(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="microseconds")


def compute_row_hash(
    prev_hash: str | None,
    *,
    occurred_at: dt.datetime,
    actor_type: str,
    actor_ref: str | None,
    action: str,
    entity_type: str,
    entity_id: str,
    before_state: dict[str, Any] | None,
    after_state: dict[str, Any] | None,
) -> str:
    payload = json.dumps(
        {
            "occurred_at": _ts(occurred_at),
            "actor_type": actor_type,
            "actor_ref": actor_ref,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "before_state": before_state,
            "after_state": after_state,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(((prev_hash or GENESIS) + payload).encode("utf-8")).hexdigest()


async def write_audit(
    session: AsyncSession,
    *,
    actor_type: str,
    action: str,
    entity_type: str,
    entity_id: str | int,
    actor_ref: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditLog:
    await advisory_xact_lock(session, AUDIT_LOCK_KEY)
    prev_hash = (
        await session.execute(text("SELECT row_hash FROM audit_log ORDER BY audit_id DESC LIMIT 1"))
    ).scalar_one_or_none()
    occurred_at = dt.datetime.now(dt.timezone.utc)
    before_n, after_n = _normalise(before), _normalise(after)
    row = AuditLog(
        occurred_at=occurred_at,
        actor_type=actor_type,
        actor_ref=actor_ref,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        before_state=before_n,
        after_state=after_n,
        prev_hash=prev_hash,
        row_hash=compute_row_hash(
            prev_hash,
            occurred_at=occurred_at,
            actor_type=actor_type,
            actor_ref=actor_ref,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            before_state=before_n,
            after_state=after_n,
        ),
    )
    session.add(row)
    await session.flush()
    return row


@dataclass(frozen=True)
class ChainReport:
    ok: bool
    rows_checked: int
    first_bad_audit_id: int | None = None
    reason: str | None = None


async def verify_chain(session: AsyncSession, *, batch_size: int = 5000) -> ChainReport:
    """Walk the whole chain in audit_id order and recompute every hash."""
    prev: str | None = None
    checked = 0
    last_id = 0
    while True:
        rows = (
            (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.audit_id > last_id)
                    .order_by(AuditLog.audit_id)
                    .limit(batch_size)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return ChainReport(ok=True, rows_checked=checked)
        for r in rows:
            if r.prev_hash != prev:
                return ChainReport(False, checked, r.audit_id, "prev_hash does not link")
            expected = compute_row_hash(
                prev,
                occurred_at=r.occurred_at,
                actor_type=r.actor_type,
                actor_ref=r.actor_ref,
                action=r.action,
                entity_type=r.entity_type,
                entity_id=r.entity_id,
                before_state=r.before_state,
                after_state=r.after_state,
            )
            if expected != r.row_hash:
                return ChainReport(False, checked, r.audit_id, "row_hash mismatch")
            prev = r.row_hash
            checked += 1
            last_id = r.audit_id
