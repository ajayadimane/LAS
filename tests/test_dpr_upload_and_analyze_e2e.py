"""End-to-end test of the real document-with-analysis flow: upload over
HTTP -> staged + queued -> worker processes it (same code
scripts/dpr_worker.py runs, only the Gemini call itself is faked, same
pattern as test_dpr_report_access.py) -> report releases over HTTP.

No test anywhere else exercises PUT .../document-with-analysis itself; this
closes that gap.
"""

import asyncio

import httpx
import pymupdf
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.dpr_agentic.jobs import get_run
from tests.conftest import DPR_TEST_DSN, bd_jwt, customer_jwt
from tests.test_bd import BD_ID, BY_PERSON_ID, REAL_PERSON
from tests.test_dpr_report_access import VENDOR_BASE, FakeGemini, dpr_worker
from tests.test_enrolment import FIELDS, PAID_TWO

PID = "9000000013"
ME = {"Authorization": f"Bearer {customer_jwt(PID)}"}
BD = {"Authorization": f"Bearer {bd_jwt(BD_ID)}"}


# Local copies of test_dpr_report_access.py's dpr_settings/dpr_client
# fixtures -- importing pytest fixtures across test modules by name
# collides with using them as parameter names (ruff F811), so each test
# module that needs this setup defines its own, same as that file does.
@pytest.fixture
def dpr_settings(dpr_pg):
    return Settings(
        vendor_base_url=VENDOR_BASE,
        vendor_program_manager_id="LAS",
        default_geo_latitude="12.9716",
        default_geo_longitude="77.5946",
        environment="test",
        context_path="",
        smtp_password=None,
        vendor_admin_user_id="test-admin",
        vendor_admin_password="test-admin-password",
        dpr_agentic_enabled=True,
        dpr_database_url=DPR_TEST_DSN,
        dpr_agentic_gemini_api_key="test-gemini-key",
        dpr_staging_encryption_key=Fernet.generate_key().decode(),
    )


@pytest.fixture
def dpr_client(dpr_settings):
    with TestClient(create_app(dpr_settings)) as c:
        yield c


def _person(**fields: str) -> dict:
    return {**REAL_PERSON, **{f"customFields.{k}": v for k, v in fields.items()}}


def _dpr_pdf_bytes() -> bytes:
    """A real, text-bearing PDF a bank would actually submit -- not a
    placeholder blob -- so inspect_document()'s LiteParse/classification
    path runs on genuine content, same as the real six sample DPRs."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "DETAILED PROJECT REPORT\n"
        "Custom Hiring Center -- Harvester\n"
        "Cost of the project: Rs 30,00,000\n"
        "Means of finance: Term loan Rs 22,50,000, Margin Rs 7,50,000",
    )
    data = doc.tobytes()
    doc.close()
    return data


def test_upload_with_analysis_runs_end_to_end(dpr_client, dpr_pg, dpr_settings, vendor):
    person_route = vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(200, json=_person(**PAID_TWO))
    )
    vendor.put(FIELDS).mock(
        return_value=httpx.Response(
            200,
            json={
                "transactionId": "t1",
                "correlationId": "c1",
                "timeStamp": "2026-11-03T00:00:00Z",
            },
        )
    )

    pdf = _dpr_pdf_bytes()
    upload = dpr_client.put(
        "/api/v1/customer/services/GEO_CHECK/document-with-analysis?filename=dpr.pdf",
        content=pdf,
        headers={**ME, "Content-Type": "application/pdf"},
    )

    assert upload.status_code == 202, upload.text
    body = upload.json()
    assert body["analysis_status"] == "QUEUED"
    assert body["wallet_storage_status"] == "STORED"
    run_id = body["run_id"]
    assert run_id

    # The real worker code path (scripts/dpr_worker.py), invoked directly
    # instead of via a separate polling process -- same function, same
    # inspect_document()/LiteParse/classify/analyze_parallel() call chain.
    key = dpr_settings.dpr_staging_encryption_key.get_secret_value().encode()
    asyncio.run(dpr_worker.process_one(dpr_pg, key, FakeGemini(), run_id, dpr_settings))

    run = get_run(dpr_pg, run_id)
    assert run["status"] == "COMPLETED", run

    report = dpr_client.get("/api/v1/customer/services/GEO_CHECK/report", headers=ME)
    assert report.status_code == 200, report.text
    assert report.json()["status"] == "completed"
    assert person_route.called


def test_bd_upload_with_analysis_runs_end_to_end(
    dpr_client, dpr_pg, dpr_settings, vendor
):
    """The BD-side equivalent: the same analysis queueing a customer gets
    themselves, now also reachable when a BD uploads on the customer's
    behalf (previously PUT /bd/.../document only stored the file -- no
    analysis was ever queued from that path)."""
    vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(200, json=_person(**PAID_TWO))
    )
    vendor.put(FIELDS).mock(
        return_value=httpx.Response(
            200,
            json={
                "transactionId": "t1",
                "correlationId": "c1",
                "timeStamp": "2026-11-03T00:00:00Z",
            },
        )
    )

    pdf = _dpr_pdf_bytes()
    upload = dpr_client.put(
        f"/api/v1/bd/customers/{PID}/services/GEO_CHECK/document-with-analysis"
        "?filename=dpr.pdf",
        content=pdf,
        headers={**BD, "Content-Type": "application/pdf"},
    )

    assert upload.status_code == 202, upload.text
    body = upload.json()
    assert body["analysis_status"] == "QUEUED"
    assert body["wallet_storage_status"] == "STORED"
    run_id = body["run_id"]
    assert run_id

    key = dpr_settings.dpr_staging_encryption_key.get_secret_value().encode()
    asyncio.run(dpr_worker.process_one(dpr_pg, key, FakeGemini(), run_id, dpr_settings))

    run = get_run(dpr_pg, run_id)
    assert run["status"] == "COMPLETED", run

    report = dpr_client.get(
        f"/api/v1/bd/customers/{PID}/services/GEO_CHECK/report", headers=BD
    )
    assert report.status_code == 200, report.text
    assert report.json()["status"] == "completed"
