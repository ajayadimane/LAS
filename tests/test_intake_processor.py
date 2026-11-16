"""The intake processor (Phase 1): emailed customer -> case -> registration
-> "choose payment" mail, with the missing-details loop.

Vendor calls are mocked with respx; mail goes to a fake mailer. Needs the
throwaway Postgres (see tests/conftest.py); skipped without it.
"""

import base64 as _b64
import hashlib as _hashlib
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import text

from app.clients.vendor_client import VendorClient
from app.core.config import Settings
from app.schemas.intake import IntakeCorrection, IntakeEvent
from app.services.admin_session import AdminSession
from app.services.intake import cases as case_store
from app.services.intake.case_data import CaseData, normalise_mobile
from app.services.intake.events import decode_attachments
from app.services.intake.payment_reply import new_text, payment_choice
from app.services.intake.processor import IntakeProcessor
from app.services.intake.store import insert_event
from app.services.mailer import Mail
from tests.conftest import (
    DPR_TEST_DSN,
    VENDOR_BASE,
    ack,
    bd_jwt,
    error_envelope,
    make_jwt,
)
from tests.test_bd import BY_PERSON_ID, REAL_PERSON
from tests.test_enrolment import FIELD_VALUE, FIELDS, TRANSFER, TRANSFER_ACK

BD_ID = "97315"
BD_EMAIL = "bd.one@example.com"
ENC_KEY = Fernet.generate_key().decode()
MOBILE = "9876543210"

LOGIN = "/IdentityAccessMgmt/user/login"
SUPERADMIN = "/IdentityAccessMgmt/user/superadmin"
ADD_USER = "/IdentityAccessMgmt/user/"
REGISTER = "/customermgmt/person/"
WALLET = "/regularWallet/"

COMPLETE = {
    "name": "Ramesh Patil",
    "mobile": "+91 98765 43210",
    "email": "ramesh@example.com",
    "street_address": "12 MG Road",
    "city": "Bengaluru",
    "state_province": "Karnataka",
    "postal_code": "560001",
    "services": ["DPR_ANALYSIS"],
    "bank_name": "SBI",
    "project_name": "Dairy unit",
}


class FakeMailer:
    configured = True

    def __init__(self) -> None:
        self.sent: list[Mail] = []

    async def send(self, mail: Mail) -> str:
        self.sent.append(mail)
        return f"<m{len(self.sent)}@las.test>"

    def message_id(self, mail: Mail) -> str:
        return f"<m{self.sent.index(mail) + 1}@las.test>"

    def to(self, address: str) -> list[Mail]:
        return [m for m in self.sent if m.to == address]


@pytest.fixture
def intake_settings(settings) -> Settings:
    return settings.model_copy(
        update={
            "intake_enabled": True,
            "intake_encryption_key": SecretStr(ENC_KEY),
            "dpr_database_url": SecretStr(DPR_TEST_DSN),
            "intake_bd_senders": {BD_EMAIL: BD_ID},
            "intake_vendor_user_id": BD_ID,
            "intake_vendor_password": SecretStr("bd-password"),
        }
    )


@pytest.fixture
def db(dpr_pg_engine):
    with dpr_pg_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE intake_case_messages, intake_cases, intake_attachments, "
                "intake_events"
            )
        )
    return dpr_pg_engine


@pytest.fixture
def mailer() -> FakeMailer:
    return FakeMailer()


@pytest.fixture
async def processor(intake_settings, db, mailer):
    async with httpx.AsyncClient(base_url=VENDOR_BASE) as http:
        client = VendorClient(http, intake_settings)
        yield IntakeProcessor(
            db, intake_settings, client, AdminSession(client, intake_settings), mailer
        )


@pytest.fixture
def routes(vendor):
    """The happy path: sign-in, register, wallet, admin, login creation."""
    return {
        "login": vendor.post(LOGIN).mock(
            return_value=httpx.Response(200, json=ack(accessToken=bd_jwt(BD_ID)))
        ),
        "superadmin": vendor.post(SUPERADMIN).mock(
            return_value=httpx.Response(
                200,
                json=ack(
                    accessToken=make_jwt(
                        {"preferred_username": "svc", "exp": 4102444800}
                    )
                ),
            )
        ),
        "register": vendor.post(REGISTER).mock(
            return_value=httpx.Response(201, json=ack())
        ),
        "wallet": vendor.post(WALLET).mock(
            return_value=httpx.Response(201, json=ack())
        ),
        "add_user": vendor.post(ADD_USER).mock(
            return_value=httpx.Response(201, json=ack())
        ),
    }


_counter = 0


def _event(customer: dict | None = None, **overrides) -> IntakeEvent:
    global _counter
    _counter += 1
    raw = {
        "version": 1,
        "event_id": f"<bd-mail-{_counter}@example.com>",
        "sender": {
            "email": BD_EMAIL,
            "bd_code": BD_ID,
            "auth": {"spf": "pass", "dkim": "pass", "dmarc": "pass"},
        },
        "thread": {"subject": "New customer"},
        "actions": [
            {
                "intent": "NEW_CUSTOMER",
                "confidence": 0.95,
                "grounded": True,
                "customers": [customer if customer is not None else COMPLETE],
            }
        ],
    }
    raw.update(overrides)
    return IntakeEvent.model_validate(raw)


async def _receive(processor, settings, event: IntakeEvent) -> dict:
    insert_event(
        processor._engine, event, decode_attachments(event, settings), ENC_KEY.encode()
    )
    return await processor.tick()


def _cases(db) -> list:
    with db.connect() as conn:
        ids = conn.execute(
            text("SELECT case_id FROM intake_cases ORDER BY created_at")
        ).all()
    return [case_store.load_case(db, str(r[0]), ENC_KEY.encode()) for r in ids]


def _event_status(db) -> list[tuple[str, str | None]]:
    with db.connect() as conn:
        return [
            (r[0], r[1])
            for r in conn.execute(
                text("SELECT status, last_error FROM intake_events ORDER BY created_at")
            )
        ]


# --- the happy path ----------------------------------------------------------


async def test_complete_email_registers_and_asks_how_the_customer_pays(
    processor, intake_settings, db, mailer, routes
):
    event = _event()
    assert await _receive(processor, intake_settings, event) == {"PROCESSED": 1}

    body = json.loads(routes["register"].calls.last.request.read())
    assert body["personId"] == MOBILE  # normalised from "+91 98765 43210"
    assert body["brokerId"] == BD_ID
    assert body["firstName"] == "Ramesh" and body["lastName"] == "Patil"
    assert body["addressPostalCode"] == "560001"
    # Signed in as the intake account, with no end user's IP to forward.
    login = routes["login"].calls.last.request
    assert json.loads(login.read())["userId"] == BD_ID
    assert login.headers["X-Original-Forwarded-For"] == "127.0.0.1"

    (case,) = _cases(db)
    assert case.state == "AWAITING_PAYMENT"
    assert case.person_id == MOBILE
    (to_bd,) = mailer.to(BD_EMAIL)
    assert f"[LAS-IN-{case.ref}]" in to_bd.subject
    assert "registered" in to_bd.subject
    assert to_bd.in_reply_to == event.event_id  # threaded on the BD's mail
    assert to_bd.auto_submitted
    assert f"LAS-PAY%20{case.ref}%20PAY_NOW" in to_bd.html
    assert f"LAS-PAY%20{case.ref}%20PAY_LATER" in to_bd.html
    assert "Rs 1,000" in to_bd.text and "Rs 295" in to_bd.text
    assert "Rs 1,295" in to_bd.text
    # The customer got their login details, and the BD gets them too.
    (to_customer,) = mailer.to("ramesh@example.com")
    password = next(
        line.split(": ", 1)[1]
        for line in to_bd.text.splitlines()
        if "Temporary password:" in line
    )
    assert f"User id: {MOBILE}" in to_bd.text
    assert password in to_customer.text
    assert password in to_bd.html
    # Never stored with the case.
    with db.connect() as conn:
        stored = conn.execute(text("SELECT data_ciphertext FROM intake_cases")).scalar()
    from cryptography.fernet import Fernet as _F

    assert password not in _F(ENC_KEY.encode()).decrypt(bytes(stored)).decode()
    assert _event_status(db) == [("PROCESSED", "CASES=1")]


async def test_reprocessing_the_same_email_does_not_open_a_second_case(
    processor, intake_settings, db, routes
):
    event = _event()
    await _receive(processor, intake_settings, event)
    with db.begin() as conn:
        conn.execute(text("UPDATE intake_events SET status = 'RECEIVED'"))
    await processor.tick()
    assert len(_cases(db)) == 1
    assert routes["register"].call_count == 1


# --- missing details ---------------------------------------------------------


async def test_missing_details_are_asked_for_then_a_reply_completes_them(
    processor, intake_settings, db, mailer, routes
):
    partial = {k: v for k, v in COMPLETE.items() if k not in ("email", "postal_code")}
    first = _event(partial)
    await _receive(processor, intake_settings, first)

    (case,) = _cases(db)
    assert case.state == "NEEDS_INFO"
    assert routes["register"].call_count == 0
    (ask,) = mailer.to(BD_EMAIL)
    assert "email address" in ask.text and "6-digit PIN code" in ask.text
    assert "Ramesh Patil" in ask.text  # what we have so far

    reply = _event(
        {"email": "ramesh@example.com", "postal_code": "560 001"},
        thread={"subject": "Re: details", "in_reply_to": "<m1@las.test>"},
    )
    reply.actions[0].intent = "CLARIFICATION_REPLY"
    await _receive(processor, intake_settings, reply)

    (case,) = _cases(db)
    assert case.state == "AWAITING_PAYMENT"
    assert routes["register"].call_count == 1
    assert "registered" in mailer.to(BD_EMAIL)[-1].subject


async def test_a_reply_is_matched_by_the_subject_code_too(
    processor, intake_settings, db, mailer, routes
):
    await _receive(processor, intake_settings, _event({**COMPLETE, "email": None}))
    (case,) = _cases(db)
    reply = _event(
        {"email": "ramesh@example.com"},
        thread={"subject": f"email for [LAS-IN-{case.ref}]"},
    )
    await _receive(processor, intake_settings, reply)
    assert _cases(db)[0].state == "AWAITING_PAYMENT"


async def test_dpr_analysis_needs_bank_and_project(
    processor, intake_settings, db, mailer
):
    customer = {
        k: v for k, v in COMPLETE.items() if k not in ("bank_name", "project_name")
    }
    await _receive(processor, intake_settings, _event(customer))
    (ask,) = mailer.to(BD_EMAIL)
    assert "bank name (for DPR Analysis)" in ask.text
    assert "project name (for DPR Analysis)" in ask.text


async def test_reminder_then_expiry(processor, intake_settings, db, mailer):
    await _receive(processor, intake_settings, _event({**COMPLETE, "email": None}))
    with db.begin() as conn:
        conn.execute(
            text("UPDATE intake_cases SET updated_at = now() - interval '25 hours'")
        )
    assert await processor.tick() == {"REMIND": 1}
    assert mailer.to(BD_EMAIL)[-1].subject.split("] ")[1].startswith("Reminder")
    assert await processor.tick() == {}  # one reminder only
    with db.begin() as conn:
        conn.execute(
            text("UPDATE intake_cases SET expires_at = now() - interval '1 minute'")
        )
    assert await processor.tick() == {"EXPIRE": 1}
    assert _cases(db)[0].state == "EXPIRED"
    assert "Request closed" in mailer.to(BD_EMAIL)[-1].subject
    # Every mail of the case is threaded on the previous one.
    assert mailer.to(BD_EMAIL)[-1].in_reply_to == "<m2@las.test>"


# --- who may send ---------------------------------------------------------------


def _stranger(**overrides) -> IntakeEvent:
    event = _event(thread={"subject": "Loan enquiry"}, **overrides)
    event.sender.email = "Stranger@Example.org"
    event.sender.bd_code = None
    event.actions = []
    return event


async def test_unknown_sender_is_told_to_contact_the_las_admin(
    processor, intake_settings, db, mailer, routes
):
    event = _stranger()
    await _receive(processor, intake_settings, event)
    assert _event_status(db) == [("IGNORED", "UNKNOWN_SENDER_REPLIED")]
    (reply,) = mailer.sent
    assert reply.to == "stranger@example.org"
    assert reply.subject == "Re: Loan enquiry"
    assert reply.in_reply_to == event.event_id
    assert reply.auto_submitted
    assert (
        "You are not a registered BD. Please contact the LAS Admin to register as a BD."
        in reply.text
    )
    assert _cases(db) == []
    assert routes["login"].call_count == 0


async def test_unknown_sender_is_answered_at_most_once_a_day(
    processor, intake_settings, db, mailer
):
    await _receive(processor, intake_settings, _stranger())
    await _receive(processor, intake_settings, _stranger())
    assert len(mailer.sent) == 1
    assert _event_status(db)[-1] == ("IGNORED", "UNKNOWN_SENDER")
    with db.begin() as conn:
        conn.execute(
            text("UPDATE intake_events SET updated_at = now() - interval '25 hours'")
        )
    await _receive(processor, intake_settings, _stranger())
    assert len(mailer.sent) == 2


async def test_admin_contact_is_added_when_configured(intake_settings, db, mailer):
    s = intake_settings.model_copy(update={"intake_admin_contact": "admin@las.test"})
    async with httpx.AsyncClient(base_url=VENDOR_BASE) as http:
        client = VendorClient(http, s)
        p = IntakeProcessor(db, s, client, AdminSession(client, s), mailer)
        await _receive(p, s, _stranger())
    assert "LAS Admin (admin@las.test) to register" in mailer.sent[0].text


async def test_an_unauthenticated_unknown_sender_gets_no_reply(
    processor, intake_settings, db, mailer
):
    event = _stranger()
    event.sender.auth.dkim = "fail"
    event.sender.auth.dmarc = "fail"
    await _receive(processor, intake_settings, event)
    assert _event_status(db) == [("IGNORED", "SENDER_NOT_AUTHENTICATED")]
    assert mailer.sent == []


async def test_sender_without_dkim_or_dmarc_pass_is_ignored(
    processor, intake_settings, db, mailer
):
    event = _event()
    event.sender.auth.dkim = "fail"
    event.sender.auth.dmarc = "none"
    await _receive(processor, intake_settings, event)
    assert _event_status(db) == [("IGNORED", "SENDER_NOT_AUTHENTICATED")]
    assert mailer.sent == []


async def test_suspected_injection_is_ignored(processor, intake_settings, db, mailer):
    await _receive(processor, intake_settings, _event(injection_suspected=True))
    assert _event_status(db) == [("IGNORED", "INJECTION_SUSPECTED")]


async def test_a_mail_with_no_new_customer_is_ignored(processor, intake_settings, db):
    event = _event()
    event.actions[0].intent = "STATUS_QUERY"
    await _receive(processor, intake_settings, event)
    assert _event_status(db) == [("IGNORED", "NO_NEW_CUSTOMER")]


# --- the vendor says no ----------------------------------------------------------


async def test_already_this_bds_customer_continues_without_registering_again(
    processor, intake_settings, db, mailer, vendor, routes
):
    routes["register"].mock(
        return_value=httpx.Response(
            409, json=error_envelope(409, "PERSON_ALREADY_EXISTS")
        )
    )
    vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(200, json={**REAL_PERSON, "brokerId.id": BD_ID})
    )
    await _receive(processor, intake_settings, _event())
    (case,) = _cases(db)
    assert case.state == "AWAITING_PAYMENT"
    assert case.data.existing_customer
    to_bd = mailer.to(BD_EMAIL)[-1]
    assert "already registered under you" in to_bd.text
    assert "Temporary password" not in to_bd.text  # their login is unchanged
    assert "joining fee if this is the customer's first payment" in to_bd.text
    assert routes["wallet"].call_count == 0


async def test_another_bds_customer_is_refused(
    processor, intake_settings, db, mailer, vendor, routes
):
    routes["register"].mock(
        return_value=httpx.Response(
            409, json=error_envelope(409, "PERSON_ALREADY_EXISTS")
        )
    )
    vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(200, json={**REAL_PERSON, "brokerId.id": "11111"})
    )
    await _receive(processor, intake_settings, _event())
    (case,) = _cases(db)
    assert case.state == "FAILED"
    assert "already used by another account" in mailer.to(BD_EMAIL)[-1].text


async def test_a_taken_mobile_reported_as_400_is_checked_like_a_409(
    processor, intake_settings, db, mailer, vendor, routes
):
    """Live sandbox, 2026-11-16: a taken mobile is 400 MOBILE_NUMBER_ALREADY_EXISTS."""
    routes["register"].mock(
        return_value=httpx.Response(
            400, json=error_envelope(400, "MOBILE_NUMBER_ALREADY_EXISTS")
        )
    )
    lookup = vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(200, json={**REAL_PERSON, "brokerId.id": BD_ID})
    )
    await _receive(processor, intake_settings, _event())
    assert lookup.call_count == 1
    (case,) = _cases(db)
    assert case.state == "AWAITING_PAYMENT" and case.data.existing_customer


async def test_a_mobile_taken_outside_las_is_refused(
    processor, intake_settings, db, mailer, vendor, routes
):
    routes["register"].mock(
        return_value=httpx.Response(
            400, json=error_envelope(400, "MOBILE_NUMBER_ALREADY_EXISTS")
        )
    )
    vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(404, json=error_envelope(404, "PERSON_NOT_FOUND"))
    )
    await _receive(processor, intake_settings, _event())
    (case,) = _cases(db)
    assert case.state == "FAILED"
    assert "already used by another account" in mailer.to(BD_EMAIL)[-1].text


async def test_a_refused_registration_is_reported(
    processor, intake_settings, db, mailer, routes
):
    routes["register"].mock(
        return_value=httpx.Response(400, json=error_envelope(400, "INVALID_EMAIL"))
    )
    await _receive(processor, intake_settings, _event())
    (case,) = _cases(db)
    assert case.state == "FAILED"
    assert "Could not register" in mailer.to(BD_EMAIL)[-1].subject


async def test_an_unknown_outcome_is_never_retried(
    processor, intake_settings, db, mailer, routes
):
    routes["register"].mock(return_value=httpx.Response(500, content=b""))
    await _receive(processor, intake_settings, _event())
    (case,) = _cases(db)
    assert case.state == "NEEDS_ATTENTION"
    assert routes["register"].call_count == 1
    assert "could not confirm" in mailer.to(BD_EMAIL)[-1].text
    await processor.tick()
    assert routes["register"].call_count == 1


async def test_wallet_failure_still_registers_and_says_so(
    processor, intake_settings, db, mailer, routes
):
    routes["wallet"].mock(return_value=httpx.Response(500, content=b""))
    await _receive(processor, intake_settings, _event())
    assert _cases(db)[0].state == "AWAITING_PAYMENT"
    assert "wallet could not be opened" in mailer.to(BD_EMAIL)[-1].text


async def test_without_an_intake_account_the_case_waits_for_attention(
    intake_settings, db, mailer, routes
):
    s = intake_settings.model_copy(update={"intake_vendor_password": None})
    async with httpx.AsyncClient(base_url=VENDOR_BASE) as http:
        client = VendorClient(http, s)
        p = IntakeProcessor(db, s, client, AdminSession(client, s), mailer)
        await _receive(p, s, _event())
    assert _cases(db)[0].state == "NEEDS_ATTENTION"
    assert routes["register"].call_count == 0


async def test_an_unexpected_error_is_retried_later_with_backoff(
    processor, intake_settings, db, monkeypatch
):
    async def boom(*_args, **_kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(processor, "_handle", boom)
    assert await _receive(processor, intake_settings, _event()) == {"RECEIVED": 1}
    with db.connect() as conn:
        row = conn.execute(
            text("SELECT status, attempts, lease_until, last_error FROM intake_events")
        ).one()
    assert row.status == "RECEIVED" and row.attempts == 1
    assert row.lease_until > datetime.now(timezone.utc) + timedelta(seconds=60)
    assert row.last_error == "RuntimeError"
    assert await processor.tick() == {}  # not due yet


# --- several customers and services ------------------------------------------------


async def test_portal_services_are_explained_not_taken(
    processor, intake_settings, db, mailer, routes
):
    await _receive(
        processor,
        intake_settings,
        _event({**COMPLETE, "services": ["DPR_ANALYSIS", "VALUE_ADDITION"]}),
    )
    (case,) = _cases(db)
    assert case.data.services == ["DPR_ANALYSIS"]
    assert "partner portal" in mailer.to(BD_EMAIL)[-1].text


async def test_two_customers_in_one_mail_become_two_cases(
    processor, intake_settings, db, routes
):
    second = {**COMPLETE, "name": "Sita Rao", "mobile": "9123456780"}
    event = _event()
    event.actions[0].customers.append(
        event.actions[0].customers[0].model_copy(update=second)
    )
    await _receive(processor, intake_settings, event)
    assert sorted(c.person_id for c in _cases(db)) == ["9123456780", MOBILE]
    assert routes["register"].call_count == 2


# --- case data -----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("9876543210", MOBILE),
        ("+91 98765 43210", MOBILE),
        ("098765-43210", MOBILE),
        ("5876543210", None),  # Indian mobiles start 6-9
        ("98765", None),
        (None, None),
    ],
)
def test_normalise_mobile(raw, expected):
    assert normalise_mobile(raw) == expected


def test_complete_data_has_nothing_missing(intake_settings):
    data = CaseData()
    from app.schemas.intake import IntakeCustomer

    data.merge_customer(IntakeCustomer(**COMPLETE), [], intake_settings)
    assert data.missing(intake_settings) == []
    req = data.to_create_request()
    assert req.mobile_number == MOBILE and req.last_name == "Patil"


# --- a refused registration reopened by the BD's reply ----------------------


async def test_a_reply_with_a_new_mobile_reopens_a_refused_case(
    processor, intake_settings, db, mailer, vendor, routes
):
    """Live, 2026-11-16: refused for a taken mobile; the BD replied with a new one."""
    taken = httpx.Response(
        400, json=error_envelope(400, "MOBILE_NUMBER_ALREADY_EXISTS")
    )
    routes["register"].mock(side_effect=[taken, httpx.Response(201, json=ack())])
    vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(404, json=error_envelope(404, "PERSON_NOT_FOUND"))
    )
    await _receive(processor, intake_settings, _event())
    (case,) = _cases(db)
    assert case.state == "FAILED"
    refused = mailer.to(BD_EMAIL)[-1]
    assert "Reply to this email with a different mobile number" in refused.text

    reply = _event(
        {"name": "Ramesh Patil", "mobile": "8618591420"},
        thread={
            "subject": f"Re: {refused.subject}",
            "in_reply_to": mailer.message_id(refused),
        },
    )
    reply.actions[0].intent = "CORRECTION"
    await _receive(processor, intake_settings, reply)

    (case,) = _cases(db)
    assert case.state == "AWAITING_PAYMENT"
    assert case.person_id == "8618591420"
    body = json.loads(routes["register"].calls.last.request.read())
    assert body["personId"] == "8618591420"
    assert body["addressCity"] == "Bengaluru"  # the rest kept from the first email
    assert "registered" in mailer.to(BD_EMAIL)[-1].subject


async def test_a_correction_field_alone_is_applied(
    processor, intake_settings, db, mailer, vendor, routes
):
    routes["register"].mock(
        side_effect=[
            httpx.Response(
                400, json=error_envelope(400, "MOBILE_NUMBER_ALREADY_EXISTS")
            ),
            httpx.Response(201, json=ack()),
        ]
    )
    vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(404, json=error_envelope(404, "PERSON_NOT_FOUND"))
    )
    await _receive(processor, intake_settings, _event())
    (case,) = _cases(db)
    reply = _event(thread={"subject": f"Re: [LAS-IN-{case.ref}] fix"})
    reply.actions[0].intent = "CORRECTION"
    reply.actions[0].customers = []
    reply.actions[0].correction = IntakeCorrection(
        field="mobile", new="+91 86185 91420"
    )
    await _receive(processor, intake_settings, reply)
    assert _cases(db)[0].person_id == "8618591420"


async def test_a_reply_without_changes_does_not_retry_a_refused_case(
    processor, intake_settings, db, mailer, routes
):
    routes["register"].mock(
        return_value=httpx.Response(400, json=error_envelope(400, "INVALID_EMAIL"))
    )
    await _receive(processor, intake_settings, _event())
    (case,) = _cases(db)
    reply = _event(thread={"subject": f"Re: [LAS-IN-{case.ref}] same again"})
    await _receive(processor, intake_settings, reply)
    assert _event_status(db)[-1] == ("IGNORED", "REPLY_WITHOUT_CHANGES")
    assert routes["register"].call_count == 1


# --- Phase 2: the payment reply, payment and documents ---------------------------


DPR_PDF = b"%PDF-1.4 the customer's DPR"


def _with_dpr(event: IntakeEvent) -> IntakeEvent:
    raw = event.model_dump()
    raw["attachments"] = [
        {
            "attachment_id": "7",
            "filename": "dpr.pdf",
            "content_type": "application/pdf",
            "size_bytes": len(DPR_PDF),
            "sha256": _hashlib.sha256(DPR_PDF).hexdigest(),
            "doc_class": "DPR",
            "content_base64": _b64.b64encode(DPR_PDF).decode(),
        }
    ]
    return IntakeEvent.model_validate(raw)


@pytest.fixture
def pay_routes(vendor, routes):
    """The vendor keeps what is written, so a read after a write sees it."""
    stored: dict[str, str] = {}

    def read(_request):
        fields = {f"customFields.{k}": v for k, v in stored.items()}
        return httpx.Response(200, json={**REAL_PERSON, "brokerId.id": BD_ID, **fields})

    def write_all(request):
        stored.update(json.loads(request.read())["customFields"])
        return httpx.Response(200, json=ack())

    def write_one(request):
        body = json.loads(request.read())
        stored[body["customFieldId"]] = body["customFieldValue"]
        return httpx.Response(200, json=ack())

    vendor.post(BY_PERSON_ID).mock(side_effect=read)
    return {
        **routes,
        "fields": vendor.put(FIELDS).mock(side_effect=write_all),
        "field_value": vendor.put(FIELD_VALUE).mock(side_effect=write_one),
        "transfer": vendor.put(TRANSFER).mock(
            return_value=httpx.Response(200, json=TRANSFER_ACK)
        ),
    }


async def _registered(processor, settings, db, mailer, *, with_dpr=True):
    event = _with_dpr(_event()) if with_dpr else _event()
    await _receive(processor, settings, event)
    (case,) = _cases(db)
    assert case.state == "AWAITING_PAYMENT"
    return case, mailer.to(BD_EMAIL)[-1]


def _bd_reply(case, to_mail, mailer, *, subject=None, body="") -> IntakeEvent:
    event = _event(
        thread={
            "subject": subject or f"Re: {to_mail.subject}",
            "in_reply_to": mailer.message_id(to_mail),
        },
        body_text=body,
    )
    event.actions = []
    return event


def _written_fields(route) -> list[dict]:
    return [json.loads(c.request.read())["customFields"] for c in route.calls]


async def test_pay_later_button_reply_pays_and_uploads_the_document(
    processor, intake_settings, db, mailer, pay_routes
):
    case, registered = await _registered(processor, intake_settings, db, mailer)
    reply = _bd_reply(
        case,
        registered,
        mailer,
        subject=f"LAS-PAY {case.ref} PAY_LATER [LAS-IN-{case.ref}]",
        body="Customer will pay later. I confirm the customer agreed and I stand "
        "guarantor (Pay later).",
    )
    assert await _receive(processor, intake_settings, reply) == {"PROCESSED": 1}

    assert pay_routes["transfer"].call_count == 1
    transfer = json.loads(pay_routes["transfer"].calls.last.request.read())
    assert "1295" in json.dumps(transfer)  # DPR Analysis 1000 + joining fee 295
    # The DPR Analysis details were written before anything was charged.
    assert pay_routes["field_value"].call_count == 2
    writes = _written_fields(pay_routes["fields"])
    assert writes[0]["DPR_Analysis_Payment"] == "Processing"  # claimed first
    assert writes[1]["DPR_Analysis_Payment"] == "Paid"
    assert writes[1]["DPR_Analysis_TxnId"] == TRANSFER_ACK["transactionId"]
    doc = json.loads(writes[2]["DPR_Analysis_Doc"])
    assert doc["name"] == "dpr.pdf"
    assert _b64.b64decode(doc["data"]) == DPR_PDF
    assert writes[2]["DPR_Analysis"] == "Docs uploaded"

    (case,) = _cases(db)
    assert case.state == "DONE"
    assert case.data.paid_amount == 1295 and case.data.paid_mode == "pay_later"
    done = mailer.to(BD_EMAIL)[-1]
    assert "Payment recorded for Ramesh Patil" in done.subject
    assert TRANSFER_ACK["transactionId"] in done.text
    assert "dpr.pdf (DPR Analysis)" in done.text
    assert _event_status(db)[-1] == ("PROCESSED", "PAYMENT_PAY_LATER")


async def test_pay_now_in_the_bds_own_words_records_without_a_transfer(
    processor, intake_settings, db, mailer, pay_routes
):
    case, registered = await _registered(processor, intake_settings, db, mailer)
    body = (
        "Customer paid by UPI, pay now please.\n\n"
        "On Mon, 16 Nov 2026 at 13:17, LAS <las@integramicro.co.in> wrote:\n"
        "> Pay later: reply PAY LATER ...\n"
    )
    await _receive(
        processor, intake_settings, _bd_reply(case, registered, mailer, body=body)
    )
    assert pay_routes["transfer"].call_count == 0
    (case,) = _cases(db)
    assert case.state == "DONE" and case.data.paid_mode == "pay_now"
    assert _written_fields(pay_routes["fields"])[0]["DPR_Analysis_Payment"] == "Paid"


async def test_an_unclear_reply_is_asked_again_and_nothing_is_charged(
    processor, intake_settings, db, mailer, pay_routes
):
    case, registered = await _registered(processor, intake_settings, db, mailer)
    reply = _bd_reply(case, registered, mailer, body="Not sure: pay now or pay later?")
    await _receive(processor, intake_settings, reply)
    assert pay_routes["fields"].call_count == 0
    assert _cases(db)[0].state == "AWAITING_PAYMENT"
    asked = mailer.to(BD_EMAIL)[-1]
    assert "How will Ramesh Patil pay?" in asked.subject
    assert f"LAS-PAY%20{case.ref}%20PAY_LATER" in asked.html


async def test_a_refused_transfer_charges_nothing_and_lets_the_bd_choose_again(
    processor, intake_settings, db, mailer, pay_routes
):
    pay_routes["transfer"].mock(
        side_effect=[
            httpx.Response(400, json=error_envelope(400, "INSUFFICIENT_BALANCE")),
            httpx.Response(200, json=TRANSFER_ACK),
        ]
    )
    case, registered = await _registered(processor, intake_settings, db, mailer)
    subject = f"LAS-PAY {case.ref} PAY_LATER"
    await _receive(
        processor, intake_settings, _bd_reply(case, registered, mailer, subject=subject)
    )
    (case,) = _cases(db)
    assert case.state == "AWAITING_PAYMENT"
    refused = mailer.to(BD_EMAIL)[-1]
    assert "Payment not completed" in refused.subject
    assert "nothing was charged" in refused.text
    # The claim was released.
    assert _written_fields(pay_routes["fields"])[-1]["DPR_Analysis_Payment"] == "NA"

    await _receive(
        processor, intake_settings, _bd_reply(case, refused, mailer, subject=subject)
    )
    assert _cases(db)[0].state == "DONE"
    assert pay_routes["transfer"].call_count == 2


async def test_an_unknown_transfer_outcome_is_never_retried(
    processor, intake_settings, db, mailer, pay_routes
):
    pay_routes["transfer"].mock(return_value=httpx.Response(500, content=b""))
    case, registered = await _registered(processor, intake_settings, db, mailer)
    subject = f"LAS-PAY {case.ref} PAY_LATER"
    await _receive(
        processor, intake_settings, _bd_reply(case, registered, mailer, subject=subject)
    )
    (case,) = _cases(db)
    assert case.state == "NEEDS_ATTENTION"
    assert "do not pay again" in mailer.to(BD_EMAIL)[-1].text
    # A second reply only gets the status.
    await _receive(
        processor, intake_settings, _bd_reply(case, registered, mailer, subject=subject)
    )
    assert pay_routes["transfer"].call_count == 1
    assert "Our team is checking" in mailer.to(BD_EMAIL)[-1].text


async def test_a_second_reply_after_payment_gets_the_status_not_a_second_charge(
    processor, intake_settings, db, mailer, pay_routes
):
    case, registered = await _registered(processor, intake_settings, db, mailer)
    subject = f"LAS-PAY {case.ref} PAY_LATER"
    for _ in range(2):
        await _receive(
            processor,
            intake_settings,
            _bd_reply(case, registered, mailer, subject=subject),
        )
    assert pay_routes["transfer"].call_count == 1
    assert "This request is complete" in mailer.to(BD_EMAIL)[-1].text
    assert _event_status(db)[-1] == ("PROCESSED", "STATUS_SENT_DONE")


async def test_a_document_core_cannot_store_is_listed_not_uploaded(
    processor, intake_settings, db, mailer, pay_routes
):
    event = _with_dpr(_event())
    event.attachments[0].content_base64 = None
    event.attachments[0].content_omitted_reason = "TOO_LARGE"
    await _receive(processor, intake_settings, event)
    (case,) = _cases(db)
    registered = mailer.to(BD_EMAIL)[-1]
    await _receive(
        processor,
        intake_settings,
        _bd_reply(case, registered, mailer, subject=f"LAS-PAY {case.ref} PAY_NOW"),
    )
    done = mailer.to(BD_EMAIL)[-1]
    assert "dpr.pdf - not received (TOO_LARGE)" in done.text
    assert all(
        "DPR_Analysis_Doc" not in w for w in _written_fields(pay_routes["fields"])
    )


@pytest.mark.parametrize(
    "subject,body,expected",
    [
        ("LAS-PAY 0123456789 PAY_LATER", "", "PAY_LATER"),
        ("Re: LAS-PAY 0123456789 pay_now [LAS-IN-0123456789]", "", "PAY_NOW"),
        ("LAS-PAY FFFFFFFFFF PAY_NOW", "", None),  # another case's ref
        ("Re: x", "Pay later please", "PAY_LATER"),
        ("Re: x", "paynow done", "PAY_NOW"),
        ("Re: x", "pay now or pay later?", None),
        ("Re: x", "ok\n\nOn Mon, LAS wrote:\n> Pay now / Pay later", None),
        ("Re: x", "Pay later\n> Pay now", "PAY_LATER"),
    ],
)
def test_payment_choice(subject, body, expected):
    assert payment_choice(subject, body, "0123456789") == expected


def test_new_text_cuts_quoted_history():
    assert new_text("Yes pay later\n-----Original Message-----\nPay now") == (
        "Yes pay later\n"
    )
