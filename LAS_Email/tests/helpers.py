"""Test helpers: build raw mails, script the fake LLM, fetch rows."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Any

from sqlalchemy import inspect, select

from las.common.db import SessionFactory
from las.common.deps import Deps
from las.common.fakes import FakeLLM
from las.common.interfaces import LLMRequest, LLMResponse

BD1 = "bd.one@example.com"
BD2 = "bd.two@example.com"

# A plausible, grounded address for tests that need a NEW_CUSTOMER onboarding to go all the way
# through without the new LAS Middleware address clarification firing: include ADDRESS_FIELDS in
# the scripted customer() dict AND this same text verbatim in the mail body (grounding requires
# both -- las/parse/ground.py).
ADDRESS_TEXT = "12 MG Road, Bengaluru, Karnataka, 560001"
ADDRESS_FIELDS = {
    "street_address": "12 MG Road",
    "city": "Bengaluru",
    "state_province": "Karnataka",
    "postal_code": "560001",
}


def make_mail(
    *,
    from_: str = BD1,
    subject: str = "New customer",
    body: str = "Hello",
    auth: str | None = "pass",
    message_id: str | None = None,
    in_reply_to: str | None = None,
    headers: dict[str, str] | None = None,
    attachments: list[tuple[str, str, bytes]] | None = None,
    html: str | None = None,
) -> bytes:
    msg = EmailMessage()
    domain = from_.rsplit("@", maxsplit=1)[-1]
    if auth is not None:
        msg["Authentication-Results"] = (
            f"mx.zohomail.com; dkim={auth} header.i=@{domain} header.s=s1; "
            f"spf={auth} smtp.mailfrom={from_}; dmarc={auth} header.from={domain}"
        )
    msg["From"] = f"Test Sender <{from_}>"
    msg["To"] = "las@integramicro.co.in"
    msg["Subject"] = subject
    msg["Message-ID"] = message_id or f"<{uuid.uuid4().hex}@{domain}>"
    msg["Date"] = format_datetime(dt.datetime.now(dt.timezone.utc))
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    for k, v in (headers or {}).items():
        msg[k] = v
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    for filename, ctype, data in attachments or []:
        maintype, _, subtype = ctype.partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return msg.as_bytes()


def action(intent: str, confidence: float = 0.95, **kw: Any) -> dict[str, Any]:
    return {"intent": intent, "confidence": confidence, **kw}


def customer(name: str | None = None, mobile: str | None = None, **kw: Any) -> dict[str, Any]:
    return {"name": name, "mobile": mobile, **kw}


def extraction(
    *actions: dict[str, Any],
    language: str = "en",
    injection: bool = False,
    all_auth: str | None = None,
) -> str:
    return json.dumps(
        {
            "language": language,
            "actions": list(actions),
            "injection_suspected": injection,
            "all_customers_authorized_statement": all_auth,
        }
    )


def script_llm(deps: Deps, *responses: str) -> FakeLLM:
    llm = deps.llm_client
    assert isinstance(llm, FakeLLM)
    for r in responses:
        llm.queue.append(
            LLMResponse(
                text=r,
                input_tokens=1200,
                output_tokens=300,
                cache_read_tokens=100,
                stop_reason="STOP",
                request_id=f"req-{uuid.uuid4().hex[:8]}",
            )
        )
    return llm


_LLM_BY_PROMPT_DEFAULTS = {"doc_relevance": '{"plausible": true, "reason": ""}'}


def llm_by_prompt(deps: Deps, mapping: dict[str, str]) -> FakeLLM:
    """Answer by prompt template: {'intent_extract': json, 'doc_classify': json,
    'doc_relevance': json}. 'doc_relevance' defaults to always-plausible when not given, so
    existing callers that don't care about it don't need to."""
    llm = deps.llm_client
    assert isinstance(llm, FakeLLM)

    def handler(req: LLMRequest) -> LLMResponse:
        text = req.system + (req.parts[0].text or "")
        key = (
            "doc_classify"
            if "You classify a single document" in text
            else (
                "doc_relevance"
                if "You check whether a document" in text
                else (
                    "batch"
                    if "spreadsheet" in req.system.lower() or "column" in req.system.lower()
                    else "intent_extract"
                )
            )
        )
        body = mapping[key] if key in mapping else _LLM_BY_PROMPT_DEFAULTS[key]
        return LLMResponse(text=body, input_tokens=800, output_tokens=120, stop_reason="STOP")

    llm.handler = handler
    return llm


async def rows(db: SessionFactory, model: Any, *where: Any) -> list[Any]:
    # Without an explicit ORDER BY, Postgres's row order isn't guaranteed and can change with the
    # planner's choice of scan/index once other tests shift table statistics -- order by primary
    # key so assertions on insertion order stay deterministic regardless of test run order.
    pk = inspect(model).primary_key
    async with db() as s:
        return list((await s.execute(select(model).where(*where).order_by(*pk))).scalars())


async def deliver(deps: Deps, raw: bytes) -> Any:
    from las.common.models import InboundEmail
    from las.ingest.persist import ingest_raw
    from las.workflow.pipeline import process_inbound

    res = await ingest_raw(deps.db, deps.store, raw)
    await process_inbound(deps, res.inbound_id)
    return (await rows(deps.db, InboundEmail, InboundEmail.inbound_id == res.inbound_id))[0]
