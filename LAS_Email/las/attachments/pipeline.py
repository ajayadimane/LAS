"""Attachment ingestion for one inbound mail (idempotent: runs once per inbound).

walk → size/allow-list → ClamAV → SHA-256 → encrypted store → row → password check → LiteParse text/OCR
→ classification. Extracted text is cached (encrypted) in the object store as
``text/<inbound_id>/<attachment_id>.txt`` so replays never re-parse. Rejected files get one T15.
"""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from las.attachments.classify import classify
from las.attachments.sniff import CSV, PDF, XLSX, is_allowed, sniff
from las.attachments.text import extract_text, pdf_info
from las.attachments.walk import Limits, Rejected, walk_attachments
from las.common.deps import Deps
from las.common.llm import Attribution
from las.common.logging import get_logger
from las.common.models import BdPartner, EmailAttachment, InboundEmail
from las.ingest.mime import ParsedMail

log = get_logger(__name__)


class AVUnavailable(Exception):
    """Scanner outage: the whole step is retried later; files are never passed unscanned."""


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[-120:] or "file"


def text_key(inbound_id: int, attachment_id: int) -> str:
    return f"text/{inbound_id}/{attachment_id}.txt"


async def load_cached_texts(deps: Deps, session: AsyncSession, inbound_id: int) -> dict[int, str]:
    ids = (
        (
            await session.execute(
                select(EmailAttachment.attachment_id).where(
                    EmailAttachment.inbound_id == inbound_id
                )
            )
        )
        .scalars()
        .all()
    )
    texts: dict[int, str] = {}
    for attachment_id in ids:
        try:
            uri = deps.store.uri_for(text_key(inbound_id, attachment_id))
            texts[attachment_id] = (await deps.store.get(uri)).decode()
        except Exception:  # noqa: BLE001 - no cached text (e.g. infected file)
            texts[attachment_id] = ""
    return texts


async def _ingest_file(
    deps: Deps,
    inbound: InboundEmail,
    *,
    filename: str,
    declared_mime: str | None,
    data: bytes,
    mime: str,
    sha: str,
) -> tuple[EmailAttachment, Rejected | None]:
    """One already allow-listed, already-deduped file: AV scan -> encrypted store ->
    EmailAttachment row (not yet added to the session) -> PDF password/page check. Shared by the
    MIME-walk bulk path (``store_attachments``) and a single portal-uploaded file
    (``store_single_attachment``)."""
    av = await deps.av.scan(data)
    if av.status == "ERROR":
        raise AVUnavailable(av.signature or "scanner error")
    prefix = "quarantine" if av.status == "INFECTED" else "att"
    uri = await deps.store.put(
        f"{prefix}/{inbound.inbound_id}/{sha}/{_safe_name(filename)}", data, content_type=mime
    )
    row = EmailAttachment(
        inbound_id=inbound.inbound_id,
        original_filename=filename[:255],
        declared_mime=declared_mime,
        detected_mime=mime,
        size_bytes=len(data),
        sha256=sha,
        storage_uri=uri,
        av_status=av.status,
    )
    if av.status == "INFECTED":
        log.warning("attachment_infected", inbound_id=inbound.inbound_id, signature=av.signature)
        return row, Rejected(filename, "failed the virus scan and was discarded")
    if mime == PDF:
        info = pdf_info(data, max_pages=0)
        row.page_count, row.is_password_protected = info.page_count, info.needs_password
        if info.needs_password:
            return row, Rejected(filename, "is password-protected — please send an unlocked copy")
    return row, None


async def store_attachments(
    deps: Deps, session: AsyncSession, inbound: InboundEmail, mail: ParsedMail, bd: BdPartner
) -> list[Rejected]:
    """Phase A (no model calls): walk, allow-list, AV, hash, encrypted store, rows. Refused files
    are recorded in ``parsed_payload["attachment_rejections"]`` and forwarded to Core LAS, which
    tells the BD.

    Rows are committed before phase B so metering rows written in their own transactions can
    reference them (a foreign key to an uncommitted row would wait on this transaction)."""
    s = deps.settings
    payload: dict[str, Any] = dict(inbound.parsed_payload or {})
    if payload.get("attachments_stored"):
        return []
    lim = Limits(
        max_depth=s.attachment_max_depth,
        max_count=s.attachment_max_count,
        max_file_bytes=s.attachment_max_bytes,
        max_zip_total=s.zip_max_total_bytes,
    )
    walked = walk_attachments(mail.message, lim)
    rejected = list(walked.rejected)
    seen: set[str] = set()
    for f in walked.files:
        mime = sniff(f.data, f.filename)
        if not is_allowed(mime):
            rejected.append(
                Rejected(
                    f.filename,
                    f"file type not accepted ({mime}); please send PDF, JPG, PNG, DOCX or XLSX",
                )
            )
            continue
        sha = hashlib.sha256(f.data).hexdigest()
        if sha in seen:
            continue  # the same file twice in one mail
        seen.add(sha)
        row, rej = await _ingest_file(
            deps,
            inbound,
            filename=f.filename,
            declared_mime=f.declared_mime,
            data=f.data,
            mime=mime,
            sha=sha,
        )
        session.add(row)
        if rej is not None:
            rejected.append(rej)
    payload.update(
        attachments_stored=True,
        nested_texts=walked.nested_texts,
        attachment_rejections=[{"filename": r.filename, "reason": r.reason} for r in rejected],
    )
    inbound.parsed_payload = payload
    await session.flush()
    return rejected


async def _analyse_row(
    deps: Deps, session: AsyncSession, inbound: InboundEmail, bd: BdPartner, row: EmailAttachment
) -> str:
    """One committed, clean, unlocked attachment row: LiteParse text/OCR (or a deterministic cell
    read for XLSX/CSV) + classification (both metered)."""
    s = deps.settings
    data = await deps.store.get(row.storage_uri)
    mime = row.detected_mime
    attribution = Attribution(
        bd_id=bd.bd_id,
        inbound_id=inbound.inbound_id,
        attachment_id=row.attachment_id,
    )
    if mime in (XLSX, CSV):
        text = extract_text(data, mime)  # cells read deterministically (batch sheets)
    else:
        try:
            parsed = await deps.llm.parse_document(deps.parser, data, mime, attribution=attribution)
            text = parsed.text
            if parsed.password_required:
                row.is_password_protected = True
            if row.page_count is None and parsed.pages:
                row.page_count = parsed.pages
        except Exception:  # noqa: BLE001 - parser failure: fall back to the plain text layer
            log.warning("document_parse_failed", attachment_id=row.attachment_id)
            text = extract_text(data, mime)
    c = await classify(
        deps.llm,
        filename=row.original_filename,
        mime=mime,
        data=data,
        text=text,
        model=s.llm_vision_model,
        attribution=attribution,
    )
    row.doc_class = c.doc_class
    row.doc_class_confidence = Decimal(str(round(c.confidence, 3)))
    await deps.store.put(
        text_key(inbound.inbound_id, row.attachment_id), text.encode(), content_type="text/plain"
    )
    return text


async def analyse_attachments(
    deps: Deps, session: AsyncSession, inbound: InboundEmail, bd: BdPartner
) -> dict[int, str]:
    """Phase B: LiteParse text/OCR and classification (both metered) for committed rows."""
    payload: dict[str, Any] = dict(inbound.parsed_payload or {})
    if payload.get("attachments_done"):
        return await load_cached_texts(deps, session, inbound.inbound_id)
    rows = (
        (
            await session.execute(
                select(EmailAttachment)
                .where(
                    EmailAttachment.inbound_id == inbound.inbound_id,
                    EmailAttachment.av_status == "CLEAN",
                )
                .order_by(EmailAttachment.attachment_id)
            )
        )
        .scalars()
        .all()
    )
    texts: dict[int, str] = {}
    for row in rows:
        if row.is_password_protected:
            continue
        texts[row.attachment_id] = await _analyse_row(deps, session, inbound, bd, row)
    payload["attachments_done"] = True
    inbound.parsed_payload = payload
    await session.flush()
    return texts
