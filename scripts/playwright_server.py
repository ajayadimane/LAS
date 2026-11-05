#!/usr/bin/env python
"""Runs the real FastAPI app for a Playwright click-through, with the vendor
faked by respx (same mocking approach as the pytest suite -- see tests/
conftest.py's `vendor` fixture) and a throwaway Postgres for DPR data.

No real vendor credentials, no real vendor network calls, and no real money
moves: PUT /transfer/systemAccountPayment is additionally faked by the stub
wallet client (vendor_wallet_stub_enabled=True; Settings itself refuses this
outside environment=local/dev/test). This script only ever sets
environment="dev".

Usage:
    DPR_TEST_DATABASE_URL=postgresql+psycopg://postgres:test@localhost:55434/dpr_test2 \
        python scripts/playwright_server.py

Seeds three customers under BD 97315 (see PID_* below): paid with two report
versions, unpaid (locked), and one whose PDF render failed. Then serves the
app on http://127.0.0.1:8111 until Ctrl-C.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import respx  # noqa: E402
import uvicorn  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.dpr_agentic import report_store  # noqa: E402
from app.services.dpr_agentic.branch_status import (  # noqa: E402
    init_branches,
    mark_report_completed,
    mark_wallet_stored,
)
from app.services.dpr_agentic.jobs import claim, engine_for, finish, submit  # noqa: E402
from app.services.dpr_agentic.migrate import apply_migrations  # noqa: E402
from app.services.dpr_agentic.schemas import AgentOutput, AnalysisResult  # noqa: E402

BD_ID = "97315"
PID_PAID = "9100000001"
PID_UNPAID = "9100000002"
PID_FAILED = "9100000003"
SERVICE = "GEO_CHECK"

BY_BROKER_ID = "/walletInfoRetrieval/customerMgmtService/getPersonDetailsByBrokerId"
BY_PERSON_ID = "/walletInfoRetrieval/customerMgmtService/getPersonDetailsByPersonId"
BALANCE_PATH = "/walletInfoRetrieval/walletTransferService/wallet/walletBalance"

_FAKE_DOC = json.dumps(
    {
        "name": "geo-check.pdf",
        "contentType": "application/pdf",
        "data": base64.b64encode(b"%PDF-1.4 a tiny seeded test document").decode(),
    }
)


def _base_person(person_id: str, **custom_fields: str) -> dict:
    fields = {
        "GeoCheck": "NA",
        "GeoCheck_Payment": "NA",
        "GeoCheck_PayMode": "NA",
        "Payment_Status": "NA",
        "Payment_Mode": "NA",
        "Doc_Status": "NA",
        "DPR_Analysis": "NA",
        **custom_fields,
    }
    return {
        **{f"customFields.{k}": v for k, v in fields.items()},
        "personId": person_id,
        "firstName": "Test",
        "lastName": person_id[-4:],
        "mobileNumber": person_id,
        "emailAddress": f"{person_id}@example.test",
        "groupId": "group1",
        "brokerId.id": BD_ID,
        "programManagerId": "LAS",
        "currentStatus.personStatus": "ACTIVE",
        "contactInformation.postalAddress.city": "Bengaluru",
        "contactInformation.postalAddress.stateProvince": "Karnataka",
        "contactInformation.postalAddress.postalCode": "560091",
        "contactInformation.postalAddress.countryCode": "IN",
        "settlementAccount.accountNumber": "1234567890",
        "settlementAccount.ifscCode": "ICIC0001010",
    }


PERSONS = {
    PID_PAID: _base_person(
        PID_PAID,
        GeoCheck="Report ready",
        GeoCheck_Payment="Paid",
        GeoCheck_PayMode="Pay now",
        GeoCheck_Doc=_FAKE_DOC,
        Payment_Status="Paid",
        Payment_Mode="Pay now",
        Doc_Status="Uploaded",
    ),
    # Document uploaded (so the report card renders) but payment never
    # verified -- the gate must lock on PAYMENT_NOT_VERIFIED, not 404 as
    # if no analysis had ever run.
    PID_UNPAID: _base_person(
        PID_UNPAID,
        GeoCheck="Docs uploaded",
        GeoCheck_Payment="NA",
        GeoCheck_Doc=_FAKE_DOC,
        Payment_Status="NA",
        Doc_Status="Uploaded",
    ),
    PID_FAILED: _base_person(
        PID_FAILED,
        GeoCheck="Report ready",
        GeoCheck_Payment="Paid",
        GeoCheck_PayMode="Pay now",
        GeoCheck_Doc=_FAKE_DOC,
        Payment_Status="Paid",
        Payment_Mode="Pay now",
        Doc_Status="Uploaded",
    ),
}


def _install_vendor_mocks() -> None:
    router = respx.mock(assert_all_called=False)
    router.start()

    def by_person_id(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        person = PERSONS.get(body.get("personId"))
        if person is None:
            return httpx.Response(
                404,
                json={
                    "errorCode": "PERSON_NOT_FOUND",
                    "errorMessage": "PERSON_NOT_FOUND",
                },
            )
        return httpx.Response(200, json=person)

    def by_broker_id(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=list(PERSONS.values()))

    def balance(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "totalAmount": 125000.0,
                "availableAmount": 118000.0,
                "lockedAmount": 7000.0,
            },
        )

    router.post(url__regex=rf".*{BY_PERSON_ID}$").mock(side_effect=by_person_id)
    router.post(url__regex=rf".*{BY_BROKER_ID}$").mock(side_effect=by_broker_id)
    router.post(url__regex=rf".*{BALANCE_PATH}$").mock(side_effect=balance)


def _completed_result(person_id: str, ref: str, sha_seed: bytes) -> AnalysisResult:
    return AnalysisResult(
        service_request_id=f"{person_id}:{SERVICE}",
        wallet_document_id=ref,
        document_sha256=hashlib.sha256(sha_seed).hexdigest(),
        status="completed",
        agent_outputs={"document": AgentOutput(summary="ok")},
        findings=[],
        unresolved_questions=[],
    )


def _seed_run(
    engine, person_id: str, sha_seed: bytes, *, fail_pdf: bool = False
) -> None:
    ref = f"wallet-person-custom-field:{person_id}:{SERVICE}"
    result = _completed_result(person_id, ref, sha_seed)
    run_id, _ = submit(
        engine,
        service_request_id=result.service_request_id,
        document_sha256=result.document_sha256,
        source_reference=ref,
    )
    init_branches(engine, run_id)
    token = claim(engine, run_id)
    if fail_pdf:
        with patch.object(report_store, "_render_cam_pdf", return_value=None):
            report_store.store_report(engine, run_id, result)
    else:
        report_store.store_report(engine, run_id, result)
    mark_wallet_stored(engine, run_id, ref)
    mark_report_completed(engine, run_id, run_id)
    finish(engine, run_id, token, success=True, artifact_ref=run_id)


def _seed(engine) -> None:
    apply_migrations(engine)

    # Paid customer: two report versions, so the "older version" scenario
    # has something real to show.
    _seed_run(engine, PID_PAID, b"playwright-seed-v1")
    _seed_run(engine, PID_PAID, b"playwright-seed-v2")

    # Document uploaded but payment never verified: the gate locks on
    # PAYMENT_NOT_VERIFIED (403), the scenario Playwright screenshots.
    _seed_run(engine, PID_UNPAID, b"playwright-seed-unpaid")

    # A real COMPLETED analysis, but the PDF render forced to fail (never a
    # real WeasyPrint hang -- this is the one deliberately-induced failure,
    # not a flake).
    _seed_run(engine, PID_FAILED, b"playwright-seed-failed", fail_pdf=True)


def main() -> None:
    dsn = os.environ.get(
        "DPR_TEST_DATABASE_URL",
        "postgresql+psycopg://postgres:test@localhost:55434/dpr_test2",
    )
    engine = engine_for(dsn)
    _seed(engine)
    _install_vendor_mocks()

    settings = Settings(
        _env_file=None,
        environment="dev",
        context_path="",
        dpr_agentic_enabled=True,
        dpr_database_url=dsn,
        vendor_wallet_stub_enabled=True,
        vendor_program_manager_id="LAS",
        vendor_wallet_tag_id="tag1",
        default_geo_latitude="12.9716",
        default_geo_longitude="77.5946",
        smtp_password=None,
    )
    app = create_app(settings)
    config = uvicorn.Config(app, host="127.0.0.1", port=8111, log_level="info")
    server = uvicorn.Server(config)
    print(
        f"Playwright server ready on http://127.0.0.1:8111 (BD {BD_ID}, "
        f"paid={PID_PAID}, unpaid={PID_UNPAID}, failed-render={PID_FAILED})"
    )
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
