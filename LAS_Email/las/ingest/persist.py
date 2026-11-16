"""Persist-before-processing: raw .eml to object storage, then an ``inbound_emails`` row.

Idempotent on Message-ID: a re-delivered mail returns the existing row and is not re-enqueued.
A mail whose headers/body can't even be parsed is still stored and recorded (as ``NEEDS_REVIEW``,
keyed on its content hash) rather than raising — one malformed, attacker-controlled ``.eml`` must
never be able to wedge the IMAP checkpoint and block every later mail.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from las.common.db import SessionFactory
from las.common.interfaces import ObjectStore
from las.common.logging import get_logger
from las.common.models import InboundEmail
from las.common.timeutil import utcnow
from las.ingest.mime import parse_mail

log = get_logger(__name__)


@dataclass(frozen=True)
class IngestResult:
    inbound_id: int
    created: bool
    processing_status: str


def _values(raw: bytes, sha: str, uri: str, now: Any, provider_uid: str | None) -> dict[str, Any]:
    try:
        parsed = parse_mail(raw)
    except Exception as exc:
        log.exception("mail_parse_failed", sha256=sha, provider_uid=provider_uid)
        return {
            "message_id": f"<{sha}@parse-failed>",
            "provider_uid": provider_uid,
            "from_address": "unknown@invalid",
            "to_addresses": [],
            "subject": None,
            "body_text": None,
            "received_at": now,
            "raw_eml_uri": uri,
            "raw_sha256": sha,
            "processing_status": "NEEDS_REVIEW",
            "error_detail": f"parse_mail failed: {exc!r}"[:2000],
        }
    return {
        "message_id": parsed.message_id,
        "provider_uid": provider_uid,
        "in_reply_to": parsed.in_reply_to,
        "references_hdr": " ".join(parsed.references) or None,
        "from_address": parsed.from_address or "unknown@invalid",
        "from_display": parsed.from_display,
        "to_addresses": parsed.to or [],
        "cc_addresses": parsed.cc or None,
        "subject": parsed.subject[:2000] if parsed.subject else None,
        "body_text": parsed.body_text,
        "received_at": now,
        "raw_eml_uri": uri,
        "raw_sha256": sha,
    }


async def ingest_raw(
    factory: SessionFactory, store: ObjectStore, raw: bytes, *, provider_uid: str | None = None
) -> IngestResult:
    sha = hashlib.sha256(raw).hexdigest()
    now = utcnow()
    # Content-addressed, so re-storing the same mail is harmless. Stored unconditionally, before
    # the (possibly failing) parse below, so a parse failure can never lose the raw mail.
    uri = await store.put(f"raw/{now:%Y/%m/%d}/{sha}.eml", raw, content_type="message/rfc822")
    values = _values(raw, sha, uri, now, provider_uid)
    async with factory() as s:
        inbound_id = (
            await s.execute(
                insert(InboundEmail)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["message_id"])
                .returning(InboundEmail.inbound_id)
            )
        ).scalar_one_or_none()
        created = inbound_id is not None
        if not created:
            row = (
                await s.execute(
                    select(InboundEmail.inbound_id, InboundEmail.processing_status).where(
                        InboundEmail.message_id == values["message_id"]
                    )
                )
            ).one()
            await s.commit()
            return IngestResult(row.inbound_id, False, row.processing_status)
        await s.commit()
        assert inbound_id is not None
        return IngestResult(inbound_id, True, values.get("processing_status", "RECEIVED"))
