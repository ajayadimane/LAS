"""Gatekeeper: auto-reply filter -> sender authentication -> BD lookup -> status.

Runs inside the caller's transaction and records the verdict on ``inbound_emails``. This service
sends no mail: an authenticated sender who is not a BD is marked NOT_REGISTERED and forwarded to
Core LAS (which replies), an inactive BD is rejected, and an unauthenticated sender is rejected
and never forwarded (no reply to a spoofable address).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from las.common.deps import Deps
from las.common.models import BdPartner, InboundEmail
from las.gate.auth import DnsAuthVerifier, evaluate_sender
from las.gate.autoreply import is_auto_generated
from las.ingest.mime import ParsedMail
from las.workflow.state import transition_inbound

Decision = Literal["CONTINUE", "IGNORED", "REJECTED"]


@dataclass(frozen=True)
class GateOutcome:
    decision: Decision
    verdict: str | None
    bd: BdPartner | None


async def run_gate(
    session: AsyncSession,
    inbound: InboundEmail,
    mail: ParsedMail,
    raw: bytes,
    deps: Deps,
    verifier: DnsAuthVerifier | None,
) -> GateOutcome:
    settings = deps.settings
    own = {settings.las_mailbox_address.lower(), settings.mail_from_address.lower()}
    if is_auto_generated(mail, own_addresses=own):
        inbound.is_auto_generated = True
        await transition_inbound(session, inbound, "IGNORED")
        return GateOutcome("IGNORED", None, None)

    auth = evaluate_sender(
        mail,
        raw,
        trusted_authserv_ids=settings.trusted_authserv_ids,
        verifier=verifier if settings.auth_dns_fallback else None,
    )
    inbound.spf_result, inbound.dkim_result, inbound.dmarc_result = auth.spf, auth.dkim, auth.dmarc
    if not auth.passed:
        inbound.sender_verdict = "AUTH_FAILED"
        await transition_inbound(
            session, inbound, "REJECTED", error_detail=f"auth failed ({auth.source})"
        )
        return GateOutcome("REJECTED", "AUTH_FAILED", None)

    bd = (
        await session.execute(select(BdPartner).where(BdPartner.email == mail.from_address))
    ).scalar_one_or_none()
    if bd is None:
        inbound.sender_verdict = "NOT_REGISTERED"
        await transition_inbound(session, inbound, "REJECTED", error_detail="sender not registered")
        return GateOutcome("REJECTED", "NOT_REGISTERED", None)
    inbound.bd_id = bd.bd_id
    if bd.status != "ACTIVE":
        inbound.sender_verdict = "SUSPENDED"
        await transition_inbound(session, inbound, "REJECTED", error_detail=f"BD {bd.status}")
        return GateOutcome("REJECTED", "SUSPENDED", bd)

    inbound.sender_verdict = "VERIFIED"
    await session.flush()
    return GateOutcome("CONTINUE", "VERIFIED", bd)
