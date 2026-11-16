"""Per-mail pipeline: gate -> attachments -> interpretation -> queued for Core LAS.

Each step is its own transaction that first locks the inbound row, checks whether the step
already ran, and records its result, so concurrent or replayed runs are safe. Nothing is executed
here: the mail and what was extracted from it go to Core LAS as one event (las/core_handoff.py),
and Core LAS does everything else -- registration, services, payment, documents and every reply.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from las.attachments.pipeline import analyse_attachments, load_cached_texts, store_attachments
from las.common.deps import Deps
from las.common.logging import get_logger
from las.common.models import BdPartner, CoreIntakeHandoff, InboundEmail, MailAction
from las.gate.auth import DnsAuthVerifier, LiveDnsAuthVerifier
from las.gate.gatekeeper import run_gate
from las.ingest.mime import ParsedMail, parse_mail
from las.parse.context import MailContext, build_context
from las.parse.interpret import interpret
from las.workflow.state import transition_inbound

log = get_logger(__name__)

TERMINAL = {"PROCESSED", "REJECTED", "NEEDS_REVIEW", "IGNORED"}


@dataclass
class PipelineResult:
    inbound_id: int
    status: str


async def _lock(session: AsyncSession, inbound_id: int) -> InboundEmail:
    # FOR KEY SHARE, not a real FOR UPDATE: the interpretation/attachment-analysis steps run a
    # metered LLM call (las/common/llm.py) while this lock is held, which records
    # ai_usage(inbound_id=...) through its own short-lived transaction on a separate connection.
    # That INSERT's FK check needs a FOR KEY SHARE lock on this same row; FOR UPDATE here would
    # conflict with it and self-deadlock the pipeline against its own metering write. This still
    # doesn't give true mutual exclusion between two concurrent runs of the same inbound_id — see
    # the pre-prod review notes.
    row = (
        await session.execute(
            select(InboundEmail)
            .where(InboundEmail.inbound_id == inbound_id)
            .with_for_update(key_share=True)
        )
    ).scalar_one()
    await session.refresh(row)
    return row


async def _bd(session: AsyncSession, bd_id: int) -> BdPartner:
    return (await session.execute(select(BdPartner).where(BdPartner.bd_id == bd_id))).scalar_one()


async def _context(deps: Deps, session: AsyncSession, inbound: InboundEmail) -> MailContext:
    texts = await load_cached_texts(deps, session, inbound.inbound_id)
    ctx = await build_context(session, inbound, texts)
    nested = (inbound.parsed_payload or {}).get("nested_texts") or []
    if nested:  # a mail forwarded as an attachment is part of the request
        ctx.new_text = "\n\n".join([ctx.new_text, *nested]).strip()
    return ctx


async def _queue_for_core(session: AsyncSession, inbound_id: int) -> None:
    await session.execute(
        pg_insert(CoreIntakeHandoff)
        .values(inbound_id=inbound_id)
        .on_conflict_do_nothing(index_elements=["inbound_id"])
    )


async def process_inbound(
    deps: Deps, inbound_id: int, verifier: DnsAuthVerifier | None = None
) -> PipelineResult:
    factory = deps.db
    verifier = verifier or LiveDnsAuthVerifier()
    raw: bytes | None = None
    mail: ParsedMail | None = None

    # 1. gate -----------------------------------------------------------------------------------
    async with factory() as s:
        inbound = await _lock(s, inbound_id)
        status = inbound.processing_status
        if status in TERMINAL:
            await s.rollback()
            return PipelineResult(inbound_id, status)
        raw = await deps.store.get(inbound.raw_eml_uri)
        mail = parse_mail(raw)
        if inbound.sender_verdict is None:
            outcome = await run_gate(s, inbound, mail, raw, deps, verifier)
            status = inbound.processing_status
            if outcome.verdict == "NOT_REGISTERED":
                # Authenticated, but not a BD we know: Core LAS replies "not a registered BD".
                # Forwarded without extraction -- no LLM spend on strangers. A sender that failed
                # authentication is never forwarded (no reply to a possibly forged address).
                await _queue_for_core(s, inbound_id)
            await s.commit()
            if outcome.decision != "CONTINUE":
                return PipelineResult(inbound_id, status)
        else:
            await s.commit()

    # 2. attachments: store rows (commit), then OCR/classify them --------------------------------
    for phase in ("store", "analyse"):
        async with factory() as s:
            inbound = await _lock(s, inbound_id)
            assert inbound.bd_id is not None
            bd = await _bd(s, inbound.bd_id)
            if phase == "store":
                await store_attachments(deps, s, inbound, mail, bd)
            else:
                await analyse_attachments(deps, s, inbound, bd)
            await s.commit()

    # 3. interpretation -------------------------------------------------------------------------
    async with factory() as s:
        inbound = await _lock(s, inbound_id)
        existing = (
            await s.execute(
                select(MailAction.action_id).where(MailAction.inbound_id == inbound_id).limit(1)
            )
        ).scalar_one_or_none()
        if existing is None and inbound.processing_status in ("RECEIVED", "FAILED"):
            mail_ctx = await _context(deps, s, inbound)
            actions = await interpret(deps, s, inbound, mail_ctx)
            await transition_inbound(s, inbound, "PARSED")
            log.info(
                "mail_interpreted",
                inbound_id=inbound_id,
                actions=[a.intent for a in actions],
                method=inbound.parse_method,
            )
        await s.commit()

    # 4. queue for Core LAS ---------------------------------------------------------------------
    # Every authenticated BD mail is forwarded, even one where nothing was understood: Core LAS
    # decides what it means (a bare payment-choice reply carries only a reference in its subject).
    async with factory() as s:
        inbound = await _lock(s, inbound_id)
        if inbound.processing_status not in TERMINAL:
            await transition_inbound(s, inbound, "PROCESSED")
        status = inbound.processing_status
        if status == "PROCESSED":
            await _queue_for_core(s, inbound_id)
        await s.commit()
        return PipelineResult(inbound_id, status)


async def mark_failed(deps: Deps, inbound_id: int, error: str) -> None:
    async with deps.db() as s:
        inbound = await _lock(s, inbound_id)
        if inbound.processing_status in TERMINAL:
            await s.rollback()
            return
        await transition_inbound(s, inbound, "FAILED", error_detail=error)
        await s.commit()
