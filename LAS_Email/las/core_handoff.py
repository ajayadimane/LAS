"""Deliver each processed mail to Core LAS as one intake event.

The pipeline (las/workflow/pipeline.py) queues a ``core_intake_handoffs`` row in the same
transaction that marks the mail PROCESSED; this dispatcher builds the event from what the gate,
attachment analysis and interpretation stored, and POSTs it to Core LAS
(``POST /internal/v1/intake/events``, contract v1, ``X-Intake-Key``).

Delivery is at-least-once and safe to repeat: Core LAS dedupes on the Message-ID and answers 409
for a repeat, which counts as delivered here. Core only stores the event on receipt, so resending
after a crash or timeout never repeats a business action.

- 202 / 409: DELIVERED.
- 400 / 413 / 422: REJECTED -- the event itself is wrong; resending it unchanged cannot help.
- anything else (401/403/404 = misconfiguration, 429, 5xx, network): retried with backoff.

Logs carry ids and status codes only, never mail content (PII).
"""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import hashlib
from typing import Any

import httpx
from sqlalchemy import or_, select

from las.common.deps import Deps
from las.common.logging import get_logger
from las.common.models import (
    BdPartner,
    CoreIntakeHandoff,
    EmailAttachment,
    InboundEmail,
    MailAction,
)
from las.common.timeutil import utcnow

log = get_logger(__name__)

EVENT_VERSION = 1
LEASE = dt.timedelta(minutes=5)
REJECT_STATUSES = frozenset({400, 413, 422})

# Core LAS's field limits (app/schemas/intake.py there): values are cut to fit so that one
# over-long LLM span can't turn the whole mail into a 422.
_CUSTOMER_LIMITS = {
    "name": 200,
    "mobile": 20,
    "email": 320,
    "street_address": 500,
    "city": 200,
    "state_province": 200,
    "postal_code": 20,
    "bank_name": 200,
    "project_name": 200,
}
_MAX_BODY_CHARS = 20_000


def _cap(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


def backoff(attempts: int) -> dt.timedelta:
    """1 min, 2, 4, ... capped at 1 hour."""
    return dt.timedelta(seconds=min(60 * 2 ** min(max(attempts - 1, 0), 6), 3600))


def _map_services(codes: list[str] | None, code_map: dict[str, str]) -> list[str]:
    out: list[str] = []
    for code in codes or []:
        mapped = code_map.get(code, code)
        if mapped not in out:
            out.append(mapped)
    return out[:20]


def _customer(c: dict[str, Any], code_map: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {k: _cap(c.get(k), n) for k, n in _CUSTOMER_LIMITS.items()}
    out["services"] = _map_services(c.get("services"), code_map)
    return out


def _action(
    a: MailAction, attachment_ids_by_name: dict[str, str], code_map: dict[str, str]
) -> dict[str, Any]:
    e = a.entities or {}
    problems = [
        p.get("field")
        for p in e.get("_problems") or []
        if p.get("kind") in ("ungrounded", "invalid") and p.get("field")
    ]
    ids = [
        attachment_ids_by_name[name.casefold()]
        for name in e.get("attachment_filenames") or []
        if name.casefold() in attachment_ids_by_name
    ]
    return {
        "intent": a.intent,
        "confidence": float(a.confidence),
        "grounded": bool(a.grounded),
        "risk_tier": a.risk_tier,
        "ungrounded_fields": sorted(set(problems))[:100],
        "customers": [_customer(c, code_map) for c in (e.get("customers") or [])[:50]],
        "services": _map_services(e.get("services"), code_map),
        "codes": list(e.get("codes") or [])[:50],
        "attachment_ids": list(dict.fromkeys(ids))[:50],
        "correction": _correction(e.get("correction")),
    }


def _correction(c: Any) -> dict[str, Any] | None:
    """A CORRECTION's field and grounded new value (services travel as a list)."""
    if not isinstance(c, dict) or c.get("field") not in ("name", "mobile", "email"):
        return None
    new = _cap(c.get("new"), 500)
    return {"field": c["field"], "new": new} if new else None


async def _attachments(deps: Deps, rows: list[EmailAttachment]) -> list[dict[str, Any]]:
    s = deps.settings
    allowed = set(s.core_intake_attachment_mimes)
    total = 0
    out: list[dict[str, Any]] = []
    for att in rows:
        reason: str | None = None
        if att.av_status != "CLEAN":
            reason = "NOT_SCANNED_CLEAN"
        elif att.is_password_protected:
            reason = "PASSWORD_PROTECTED"
        elif att.detected_mime not in allowed:
            reason = "TYPE_NOT_ACCEPTED"
        elif att.size_bytes > s.core_intake_attachment_max_bytes:
            reason = "TOO_LARGE"
        elif total + att.size_bytes > s.core_intake_attachment_total_max_bytes:
            reason = "TOTAL_TOO_LARGE"
        content: str | None = None
        if reason is None:
            data = await deps.store.get(att.storage_uri)
            if hashlib.sha256(data).hexdigest() != att.sha256:
                reason = "CHECKSUM_MISMATCH"
            else:
                total += att.size_bytes
                content = base64.b64encode(data).decode("ascii")
        out.append(
            {
                "attachment_id": str(att.attachment_id),
                "filename": att.original_filename[:255],
                "content_type": att.detected_mime,
                "size_bytes": att.size_bytes,
                "sha256": att.sha256,
                "doc_class": att.doc_class,
                "class_confidence": (
                    float(att.doc_class_confidence)
                    if att.doc_class_confidence is not None
                    else None
                ),
                "av_status": att.av_status,
                "password_protected": bool(att.is_password_protected),
                "content_base64": content,
                "content_omitted_reason": reason,
            }
        )
    return out


async def build_event(deps: Deps, inbound_id: int) -> dict[str, Any]:
    """The v1 intake event for one processed mail (attachment bytes included where allowed).

    Built inside one read-only session: the rows must stay attached while they are read.
    """
    code_map = deps.settings.core_service_code_map
    async with deps.db() as session:
        inbound = (
            await session.execute(select(InboundEmail).where(InboundEmail.inbound_id == inbound_id))
        ).scalar_one()
        bd = (
            (
                await session.execute(select(BdPartner).where(BdPartner.bd_id == inbound.bd_id))
            ).scalar_one_or_none()
            if inbound.bd_id is not None
            else None
        )
        actions = (
            (
                await session.execute(
                    select(MailAction)
                    .where(MailAction.inbound_id == inbound_id)
                    .order_by(MailAction.seq)
                )
            )
            .scalars()
            .all()
        )
        attachments = (
            (
                await session.execute(
                    select(EmailAttachment)
                    .where(EmailAttachment.inbound_id == inbound_id)
                    .order_by(EmailAttachment.attachment_id)
                )
            )
            .scalars()
            .all()
        )

        by_name = {a.original_filename.casefold(): str(a.attachment_id) for a in attachments}
        payload = inbound.parsed_payload or {}
        return {
            "version": EVENT_VERSION,
            "event_id": inbound.message_id,
            "received_at": inbound.received_at.isoformat() if inbound.received_at else None,
            "sender": {
                "email": inbound.from_address,
                "bd_code": bd.ibkart_bd_code if bd else None,
                "auth": {
                    "spf": inbound.spf_result,
                    "dkim": inbound.dkim_result,
                    "dmarc": inbound.dmarc_result,
                },
            },
            "thread": {
                "subject": (inbound.subject or "")[:1000],
                "in_reply_to": inbound.in_reply_to,
                "references": (inbound.references_hdr or "").split()[:100],
            },
            "body_text": (inbound.body_text or "")[:_MAX_BODY_CHARS],
            "language": inbound.detected_language,
            "injection_suspected": bool(payload.get("injection_suspected")),
            "actions": [_action(a, by_name, code_map) for a in actions[:20]],
            "attachments": await _attachments(deps, list(attachments)[:50]),
            # Files refused before storage (type, size, zip limits): Core tells the BD.
            "rejected_attachments": [
                {"filename": str(r.get("filename", ""))[:255], "reason": str(r.get("reason", ""))[:300]}
                for r in (payload.get("attachment_rejections") or [])[:50]
            ],
        }


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:300]
    return str(body)[:300]


async def _claim(deps: Deps, limit: int) -> list[tuple[int, int]]:
    """Claim due rows (and IN_FLIGHT rows whose lease ran out -- resending is safe)."""
    now = utcnow()
    async with deps.db() as session:
        rows = (
            (
                await session.execute(
                    select(CoreIntakeHandoff)
                    .where(
                        or_(
                            (CoreIntakeHandoff.status == "PENDING")
                            & (CoreIntakeHandoff.next_attempt_at <= now),
                            (CoreIntakeHandoff.status == "IN_FLIGHT")
                            & (CoreIntakeHandoff.lease_until < now),
                        )
                    )
                    .order_by(CoreIntakeHandoff.handoff_id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        claimed = []
        for row in rows:
            row.status = "IN_FLIGHT"
            row.attempts += 1
            row.lease_until = now + LEASE
            row.updated_at = now
            claimed.append((row.handoff_id, row.inbound_id))
        await session.commit()
    return claimed


async def _record(
    deps: Deps,
    handoff_id: int,
    status: str,
    *,
    status_code: int | None = None,
    error: str | None = None,
    core_id: str | None = None,
) -> None:
    now = utcnow()
    async with deps.db() as session:
        row = (
            await session.execute(
                select(CoreIntakeHandoff)
                .where(CoreIntakeHandoff.handoff_id == handoff_id)
                .with_for_update()
            )
        ).scalar_one()
        row.status = status
        row.lease_until = None
        row.last_status_code = status_code
        row.last_error = (error or "")[:500] or None
        row.updated_at = now
        if core_id:
            row.core_intake_event_id = core_id
        if status == "DELIVERED":
            row.delivered_at = now
        if status == "PENDING":
            row.next_attempt_at = now + backoff(row.attempts)
        await session.commit()


async def deliver_one(
    deps: Deps, client: httpx.AsyncClient, handoff_id: int, inbound_id: int
) -> str:
    s = deps.settings
    try:
        event = await build_event(deps, inbound_id)
    except Exception as exc:  # a local fault: keep the row, retry later
        log.exception("core_intake_build_failed", handoff_id=handoff_id, inbound_id=inbound_id)
        await _record(deps, handoff_id, "PENDING", error=f"build: {type(exc).__name__}")
        return "PENDING"
    try:
        response = await client.post(
            s.core_intake_url,
            json=event,
            headers={"X-Intake-Key": s.core_intake_api_key.get_secret_value()},
        )
    except httpx.HTTPError as exc:
        log.warning("core_intake_unreachable", handoff_id=handoff_id, error=type(exc).__name__)
        await _record(deps, handoff_id, "PENDING", error=f"network: {type(exc).__name__}")
        return "PENDING"

    code = response.status_code
    if code in (202, 409):
        core_id = None
        with contextlib.suppress(ValueError, AttributeError):
            core_id = str(response.json().get("intake_event_id") or "") or None
        await _record(deps, handoff_id, "DELIVERED", status_code=code, core_id=core_id)
        log.info(
            "core_intake_delivered",
            handoff_id=handoff_id,
            inbound_id=inbound_id,
            status_code=code,
            core_intake_event_id=core_id,
        )
        return "DELIVERED"
    message = _error_message(response)
    if code in REJECT_STATUSES:
        log.error(
            "core_intake_rejected",
            handoff_id=handoff_id,
            inbound_id=inbound_id,
            status_code=code,
            error=message,
        )
        await _record(deps, handoff_id, "REJECTED", status_code=code, error=message)
        return "REJECTED"
    log.warning("core_intake_retry", handoff_id=handoff_id, inbound_id=inbound_id, status_code=code)
    await _record(deps, handoff_id, "PENDING", status_code=code, error=message)
    return "PENDING"


async def dispatch_core_intake(
    deps: Deps, limit: int = 20, *, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, int]:
    s = deps.settings
    if not s.core_intake_url:
        log.error("core_intake_url_not_configured")
        return {"not_configured": 1}
    claimed = await _claim(deps, limit)
    results: dict[str, int] = {"claimed": len(claimed)}
    if not claimed:
        return results
    async with httpx.AsyncClient(
        timeout=s.core_intake_timeout_seconds,
        verify=s.core_intake_verify_ssl,
        transport=transport,
    ) as client:
        for handoff_id, inbound_id in claimed:
            outcome = await deliver_one(deps, client, handoff_id, inbound_id)
            results[outcome] = results.get(outcome, 0) + 1
    return results
