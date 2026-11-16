"""Mails are extracted here and delivered to Core LAS; this service executes, sends and registers
nothing (las/core_handoff.py, las/workflow/pipeline.py)."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
from typing import Any

import httpx
import pytest

from las.common.deps import Deps
from las.common.models import (
    CoreIntakeHandoff,
    InboundEmail,
)
from las.common.settings import Settings
from las.common.timeutil import utcnow
from las.core_handoff import _correction, backoff, dispatch_core_intake
from tests.docs import pdf
from tests.helpers import (
    ADDRESS_FIELDS,
    ADDRESS_TEXT,
    action,
    customer,
    deliver,
    extraction,
    llm_by_prompt,
    make_mail,
    rows,
)

CORE_URL = "https://core.test/las-api/internal/v1/intake/events"
CORE_KEY = "test-core-intake-key"
MOBILE = "9876543210"


@pytest.fixture
def settings(test_db_url: str) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        env="test",
        database_url=test_db_url,
        av_scanner="fake",
        document_parser="fake",
        auth_dns_fallback=False,
        core_intake_url=CORE_URL,
        core_intake_api_key=CORE_KEY,
    )


def _script_new_customer(deps: Deps, services: list[str]) -> None:
    llm_by_prompt(
        deps,
        {
            "intent_extract": extraction(
                action(
                    "NEW_CUSTOMER",
                    customers=[
                        customer(
                            "Ramesh Patil",
                            MOBILE,
                            services=services,
                            services_stated_explicitly=True,
                            onboarding_requested_explicitly=True,
                            bank_name="SBI",
                            project_name="Dairy unit",
                            **ADDRESS_FIELDS,
                        )
                    ],
                    attachment_filenames=["dpr.pdf"],
                )
            )
        },
    )


async def _deliver_new_customer(deps: Deps, services: list[str] | None = None) -> InboundEmail:
    _script_new_customer(deps, services or ["DPR_ANALYSIS"])
    return await deliver(
        deps,
        make_mail(
            body=(
                f"Onboard Ramesh Patil {MOBILE}, {ADDRESS_TEXT}, for DPR analysis. "
                "Loan with SBI for the Dairy unit project. DPR attached."
            ),
            attachments=[("dpr.pdf", "application/pdf", pdf("Detailed Project Report"))],
        ),
    )


class _Core:
    """A fake Core LAS endpoint recording what it was sent."""

    def __init__(self, status: int = 202, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = body if body is not None else {"intake_event_id": "core-1"}
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self.status, json=self.body)

        return httpx.MockTransport(handler)

    def event(self, i: int = -1) -> dict[str, Any]:
        return json.loads(self.requests[i].content)


async def test_mail_is_queued_for_core(deps: Deps) -> None:
    inbound = await _deliver_new_customer(deps)
    assert inbound.processing_status == "PROCESSED"
    (handoff,) = await rows(deps.db, CoreIntakeHandoff)
    assert handoff.inbound_id == inbound.inbound_id
    assert handoff.status == "PENDING"


async def test_event_carries_the_extraction_and_the_document(deps: Deps) -> None:
    inbound = await _deliver_new_customer(deps, services=["DPR_ANALYSIS", "DPR_GEN"])
    core = _Core()
    result = await dispatch_core_intake(deps, transport=core.transport())
    assert result == {"claimed": 1, "DELIVERED": 1}

    request = core.requests[0]
    assert str(request.url) == CORE_URL
    assert request.headers["X-Intake-Key"] == CORE_KEY
    event = core.event()
    assert event["version"] == 1
    assert event["event_id"] == inbound.message_id
    assert event["sender"]["bd_code"] == "BD-TEST-001"
    assert event["sender"]["auth"]["dkim"] == "pass"
    (act,) = event["actions"]
    assert act["intent"] == "NEW_CUSTOMER"
    (cust,) = act["customers"]
    assert cust["mobile"] == MOBILE
    assert cust["postal_code"] == ADDRESS_FIELDS["postal_code"]
    assert cust["bank_name"] == "SBI"
    assert cust["project_name"] == "Dairy unit"
    # Our codes are mapped to Core LAS's.
    assert cust["services"] == ["DPR_ANALYSIS", "DPR_GENERATION"]
    (att,) = event["attachments"]
    assert act["attachment_ids"] == [att["attachment_id"]]
    data = base64.b64decode(att["content_base64"])
    assert hashlib.sha256(data).hexdigest() == att["sha256"]
    assert att["content_omitted_reason"] is None

    (handoff,) = await rows(deps.db, CoreIntakeHandoff)
    assert handoff.status == "DELIVERED"
    assert handoff.core_intake_event_id == "core-1"
    assert handoff.last_status_code == 202


async def test_a_409_from_core_means_already_delivered(deps: Deps) -> None:
    await _deliver_new_customer(deps)
    core = _Core(409, {"intake_event_id": "core-1", "duplicate": True})
    assert await dispatch_core_intake(deps, transport=core.transport()) == {
        "claimed": 1,
        "DELIVERED": 1,
    }


async def test_a_422_is_rejected_and_not_retried(deps: Deps) -> None:
    await _deliver_new_customer(deps)
    core = _Core(422, {"error": {"message": "actions.0.confidence: too high"}})
    await dispatch_core_intake(deps, transport=core.transport())
    (handoff,) = await rows(deps.db, CoreIntakeHandoff)
    assert handoff.status == "REJECTED"
    assert handoff.last_error == "actions.0.confidence: too high"
    assert await dispatch_core_intake(deps, transport=core.transport()) == {"claimed": 0}


@pytest.mark.parametrize("status", [403, 429, 503])
async def test_other_failures_are_retried_with_backoff(deps: Deps, status: int) -> None:
    await _deliver_new_customer(deps)
    core = _Core(status, {"error": {"message": "later"}})
    before = utcnow()
    await dispatch_core_intake(deps, transport=core.transport())
    (handoff,) = await rows(deps.db, CoreIntakeHandoff)
    assert handoff.status == "PENDING"
    assert handoff.attempts == 1
    assert handoff.next_attempt_at >= before + dt.timedelta(seconds=59)
    # Not due yet: the next run claims nothing.
    assert await dispatch_core_intake(deps, transport=core.transport()) == {"claimed": 0}


async def test_network_failure_is_retried(deps: Deps) -> None:
    await _deliver_new_customer(deps)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    await dispatch_core_intake(deps, transport=httpx.MockTransport(boom))
    (handoff,) = await rows(deps.db, CoreIntakeHandoff)
    assert handoff.status == "PENDING"
    assert handoff.last_error == "network: ConnectError"


async def test_documents_core_cannot_store_go_as_metadata_only(
    deps: Deps, settings: Settings
) -> None:
    deps.settings.core_intake_attachment_max_bytes = 10
    await _deliver_new_customer(deps)
    core = _Core()
    await dispatch_core_intake(deps, transport=core.transport())
    (att,) = core.event()["attachments"]
    assert att["content_base64"] is None
    assert att["content_omitted_reason"] == "TOO_LARGE"
    assert att["size_bytes"] > 10


async def test_unregistered_sender_is_forwarded_without_extraction(deps: Deps) -> None:
    inbound = await deliver(deps, make_mail(from_="stranger@example.org", body="Need a loan"))
    assert inbound.processing_status == "REJECTED"
    assert inbound.sender_verdict == "NOT_REGISTERED"
    (handoff,) = await rows(deps.db, CoreIntakeHandoff)
    assert handoff.inbound_id == inbound.inbound_id

    core = _Core()
    await dispatch_core_intake(deps, transport=core.transport())
    event = core.event()
    assert event["sender"]["email"] == "stranger@example.org"
    assert event["sender"]["bd_code"] is None
    assert event["sender"]["auth"]["dkim"] == "pass"
    assert event["actions"] == [] and event["attachments"] == []


async def test_unauthenticated_sender_is_never_forwarded(deps: Deps) -> None:
    inbound = await deliver(
        deps, make_mail(from_="stranger@example.org", body="Need a loan", auth="fail")
    )
    assert inbound.processing_status == "REJECTED"
    assert await rows(deps.db, CoreIntakeHandoff) == []


def test_backoff_grows_and_is_capped() -> None:
    assert backoff(1) == dt.timedelta(minutes=1)
    assert backoff(2) == dt.timedelta(minutes=2)
    assert backoff(50) == dt.timedelta(hours=1)


def test_handoff_schedule_runs_no_business_job_and_sends_no_mail() -> None:
    from las.worker.celery_app import SCHEDULE

    tasks = {entry["task"] for entry in SCHEDULE.values()}
    assert "las.jobs.core_intake_handoff" in tasks
    for forbidden in (
        "las.outbox.dispatch",
        "las.jobs.onboarding",
        "las.jobs.middleware_onboarding",
        "las.jobs.billing",
        "las.jobs.payments",
    ):
        assert forbidden not in tasks


def test_corrections_of_name_mobile_and_email_are_forwarded() -> None:
    assert _correction({"field": "mobile", "old": "97", "new": "8618591420"}) == {
        "field": "mobile",
        "new": "8618591420",
    }
    assert _correction({"field": "services", "new": "DPR_GEN"}) is None  # sent as a list
    assert _correction({"field": "email", "new": None}) is None
    assert _correction(None) is None
