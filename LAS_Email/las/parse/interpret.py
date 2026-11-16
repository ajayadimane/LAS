"""Interpretation: candidates → extraction (with escalation) → grounding → policy → mail_actions."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from las.common.deps import Deps
from las.common.llm import Attribution, LLMFailed
from las.common.logging import get_logger
from las.common.models import InboundEmail, MailAction
from las.parse.candidates import Candidates, extract_candidates
from las.parse.context import MailContext
from las.parse.extract import extract_actions
from las.parse.ground import GroundedAction, Grounder
from las.parse.policy import contradictions, decide, injection_heuristic, normalise_actions
from las.parse.schema import Extraction

log = get_logger(__name__)


def needs_escalation(
    ext: Extraction, grounded: list[GroundedAction], *, threshold: float, max_actions: int
) -> list[str]:
    reasons = []
    if any(a.confidence < threshold for a in ext.actions):
        reasons.append("low confidence")
    if len(ext.actions) > max_actions:
        reasons.append("many actions")
    if any(g.has_ungrounded() for g in grounded):
        reasons.append("grounding failed")
    return reasons


def make_grounder(ctx: MailContext, cands: Candidates) -> Grounder:
    return Grounder(
        ctx,
        cands,
        active_services={s.service_code for s in ctx.catalog},
        synonyms=ctx.synonyms,
        display_names={s.service_code: s.display_name for s in ctx.catalog},
    )


async def interpret(
    deps: Deps, session: AsyncSession, inbound: InboundEmail, ctx: MailContext
) -> list[MailAction]:
    s = deps.settings
    cands = extract_candidates(
        ctx.source_text(), own_addresses={s.las_mailbox_address, s.mail_from_address}
    )
    attribution = Attribution(
        bd_id=inbound.bd_id,
        inbound_id=inbound.inbound_id,
    )
    grounder = make_grounder(ctx, cands)
    llm = deps.llm
    method = "LLM"
    model = s.llm_extraction_model
    try:
        outcome = await extract_actions(llm, ctx, cands, model=model, attribution=attribution)
        ext = outcome.value
        grounded = [grounder.ground(a, ext) for a in ext.actions]
        reasons = needs_escalation(
            ext,
            grounded,
            threshold=s.llm_confidence_threshold,
            max_actions=s.llm_escalation_action_count,
        )
        if reasons:
            log.info("extraction_escalated", inbound_id=inbound.inbound_id, reasons=reasons)
            try:
                outcome = await extract_actions(
                    llm, ctx, cands, model=s.llm_escalation_model, attribution=attribution
                )
                ext = outcome.value
                grounded = [grounder.ground(a, ext) for a in ext.actions]
                method, model = "LLM_ESCALATED", s.llm_escalation_model
            except LLMFailed:
                log.warning("escalation_failed_using_primary", inbound_id=inbound.inbound_id)
    except LLMFailed:
        outcome = await extract_actions(
            llm, ctx, cands, model=s.llm_escalation_model, attribution=attribution
        )
        ext = outcome.value
        grounded = [grounder.ground(a, ext) for a in ext.actions]
        method, model = "LLM_ESCALATED", s.llm_escalation_model

    grounded = normalise_actions(grounded)
    attachment_text = "\n".join(a.text for a in ctx.attachments)
    injection = ext.injection_suspected or injection_heuristic(
        "\n".join([ctx.subject, ctx.new_text, ctx.quoted_text, attachment_text])
    )
    contra = contradictions(grounded)
    actions: list[MailAction] = []
    for seq, g in enumerate(grounded, start=1):
        d = decide(
            g,
            threshold=s.llm_confidence_threshold,
            injection=injection,
            contradiction=contra,
            escalated=method == "LLM_ESCALATED",
        )
        entities = dict(
            g.entities,
            _policy={"missing": d.missing, "reasons": d.reasons, "terminal": d.terminal},
            _problems=[{"field": p.field, "kind": p.kind, "detail": p.detail} for p in g.problems],
        )
        row = MailAction(
            inbound_id=inbound.inbound_id,
            seq=seq,
            intent=g.intent,
            entities=entities,
            evidence=g.evidence,
            grounded=g.grounded,
            confidence=Decimal(str(round(g.confidence, 3))),
            risk_tier=d.tier,
        )
        session.add(row)
        actions.append(row)
    inbound.parsed_payload = {
        **(inbound.parsed_payload or {}),
        "model": model,
        "method": method,
        "injection_suspected": injection,
        "extraction": ext.model_dump(mode="json"),
    }
    inbound.parse_method = method
    inbound.detected_language = ext.language
    inbound.parse_confidence = (
        Decimal(str(round(min(g.confidence for g in grounded), 3))) if grounded else None
    )
    inbound.primary_intent = grounded[0].intent if grounded else None
    await session.flush()
    return actions
