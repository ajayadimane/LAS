from sqlalchemy import select

from las.common.db import SessionFactory
from las.common.deps import Deps
from las.common.models import BdPartner, CoreIntakeHandoff, InboundEmail
from las.ingest.persist import ingest_raw
from las.workflow.pipeline import process_inbound
from tests.helpers import BD1, action, customer, extraction, make_mail, rows, script_llm


async def _run(deps: Deps, raw: bytes) -> InboundEmail:
    res = await ingest_raw(deps.db, deps.store, raw)
    await process_inbound(deps, res.inbound_id)
    return (await rows(deps.db, InboundEmail, InboundEmail.inbound_id == res.inbound_id))[0]


async def test_spoofed_from_with_failing_dmarc_is_rejected_silently(
    deps: Deps, db: SessionFactory
) -> None:
    ib = await _run(deps, make_mail(from_=BD1, auth="fail", body="Onboard Ramesh 9876543210"))
    assert (ib.processing_status, ib.sender_verdict, ib.dmarc_result) == (
        "REJECTED",
        "AUTH_FAILED",
        "fail",
    )
    # Never forwarded, so Core never replies to a spoofable address.
    assert await rows(db, CoreIntakeHandoff) == []


async def test_untrusted_authentication_results_header_is_ignored(
    deps: Deps, db: SessionFactory
) -> None:
    raw = make_mail(
        from_=BD1,
        auth=None,
        headers={
            "Authentication-Results": "evil.example; dkim=pass header.i=@example.com; dmarc=pass header.from=example.com"
        },
    )
    ib = await _run(deps, raw)
    assert ib.sender_verdict == "AUTH_FAILED"


async def test_auto_reply_gets_no_response(deps: Deps, db: SessionFactory) -> None:
    for hdr in (
        {"Auto-Submitted": "auto-replied"},
        {"X-Autoreply": "yes"},
        {"Precedence": "bulk"},
        {"Precedence": "auto_reply"},
    ):
        ib = await _run(deps, make_mail(subject="Out of office", headers=hdr))
        assert ib.processing_status == "IGNORED" and ib.is_auto_generated
    assert await rows(db, CoreIntakeHandoff) == []


async def test_unregistered_sender_is_forwarded_to_core_exactly_once(
    deps: Deps, db: SessionFactory
) -> None:
    """Core LAS answers "not a registered BD"; replay/redelivery never forwards the mail twice,
    and no extraction is spent on a stranger."""
    stranger = "someone@example.org"
    raw = make_mail(from_=stranger, body="please help me with a loan")
    first = await _run(deps, raw)
    await process_inbound(deps, first.inbound_id)  # replay
    await _run(deps, raw)  # redelivery
    assert len(await rows(db, CoreIntakeHandoff)) == 1
    assert first.sender_verdict == "NOT_REGISTERED"
    assert deps.llm_client.requests == []  # type: ignore[attr-defined]


async def test_suspended_bd_is_rejected(deps: Deps, db: SessionFactory) -> None:
    async with db() as s:
        bd = (await s.execute(select(BdPartner).where(BdPartner.email == BD1))).scalar_one()
        bd.status = "SUSPENDED"
        await s.commit()
    ib = await _run(deps, make_mail(body="new customer"))
    assert (ib.processing_status, ib.sender_verdict) == ("REJECTED", "SUSPENDED")
    assert await rows(db, CoreIntakeHandoff) == []


async def test_duplicate_message_id_processed_once(deps: Deps, db: SessionFactory) -> None:
    script_llm(deps, extraction(action("STATUS_QUERY", customers=[customer(name="Ramesh")])))
    raw = make_mail(body="status of Ramesh please")
    a = await ingest_raw(deps.db, deps.store, raw)
    b = await ingest_raw(deps.db, deps.store, raw)
    assert a.inbound_id == b.inbound_id and a.created and not b.created
    await process_inbound(deps, a.inbound_id)
    await process_inbound(deps, a.inbound_id)
    assert len(await rows(db, InboundEmail)) == 1
    assert len(await rows(db, CoreIntakeHandoff)) == 1
    assert len(deps.llm_client.requests) == 1  # type: ignore[attr-defined]
