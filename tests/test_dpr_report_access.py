"""HTTP-level tests for the DPR report-access endpoints (customer + BD).

Exercises the real stack: route -> report_service.access_report -> jobs /
branch_status / report_store (real Postgres, see tests/conftest.py) ->
report_access.evaluate_report_access, with the vendor mocked via respx.
"""

import asyncio
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.dpr_agentic.branch_status import (
    init_branches,
    mark_report_completed,
    mark_wallet_stored,
)
from app.services.dpr_agentic.jobs import claim, finish, get_run, list_claimable, submit
from app.services.dpr_agentic.report_store import list_report_versions, store_report
from app.services.dpr_agentic.schemas import AgentOutput, AnalysisResult
from tests.conftest import DPR_TEST_DSN, ack, bd_jwt, customer_jwt
from tests.test_bd import BY_PERSON_ID, REAL_PERSON
from tests.test_enrolment import FIELDS

# scripts/ isn't a package; load dpr_worker.py by path (same pattern it uses
# for itself) so its own functions run, not a re-implementation of them.
_WORKER_SPEC = importlib.util.spec_from_file_location(
    "dpr_worker", Path(__file__).resolve().parents[1] / "scripts" / "dpr_worker.py"
)
dpr_worker = importlib.util.module_from_spec(_WORKER_SPEC)
_WORKER_SPEC.loader.exec_module(dpr_worker)


class FakeGemini:
    """Matches test_dpr_worker.py's pattern: the real LangGraph pipeline
    runs, only the LLM call itself is faked."""

    async def generate(self, prompt, context):
        return AgentOutput(summary="checked")

    async def extract(self, prompt, context):
        from app.services.dpr_agentic.financial_schema import ProjectFinancials

        return ProjectFinancials()


VENDOR_BASE = "https://vendor.test:8080"
PID = "9000000013"
BD_ID = "97315"
ME = {"Authorization": f"Bearer {customer_jwt(PID)}"}
BD_AUTH = {"Authorization": f"Bearer {bd_jwt(BD_ID)}"}


def _person(**fields: str) -> dict:
    return {**REAL_PERSON, **{f"customFields.{k}": v for k, v in fields.items()}}


def _paid_person(vendor, status: str = "Report ready") -> None:
    vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(
            200, json=_person(GeoCheck=status, GeoCheck_Payment="Paid")
        )
    )


def _report(sha: str) -> AnalysisResult:
    return AnalysisResult(
        service_request_id="SR-1",
        wallet_document_id="W-1",
        document_sha256=sha,
        status="completed",
        agent_outputs={"document": AgentOutput(summary="ok")},
        findings=[],
        unresolved_questions=[],
    )


def _submit_completed_run(dpr_pg, *, source_reference: str, sha: str) -> str:
    run_id, _ = submit(
        dpr_pg,
        service_request_id=f"{PID}:GEO_CHECK",
        document_sha256=sha,
        source_reference=source_reference,
    )
    init_branches(dpr_pg, run_id)
    token = claim(dpr_pg, run_id)
    store_report(dpr_pg, run_id, _report(sha))
    mark_wallet_stored(dpr_pg, run_id, source_reference)
    mark_report_completed(dpr_pg, run_id, run_id)
    finish(dpr_pg, run_id, token, success=True, artifact_ref=run_id)
    return run_id


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


# --- feature flag / configuration -----------------------------------------


def test_customer_report_404s_when_the_feature_is_disabled(client):
    response = client.get("/api/v1/customer/services/GEO_CHECK/report", headers=ME)
    assert response.status_code == 404


def test_customer_report_503s_when_no_database_is_configured():
    settings = Settings(
        environment="test",
        context_path="",
        dpr_agentic_enabled=True,
        # Explicit None regardless of .env -- this test asserts the
        # behavior when the database is NOT configured.
        dpr_database_url=None,
    )
    with TestClient(create_app(settings)) as c:
        response = c.get("/api/v1/customer/services/GEO_CHECK/report", headers=ME)
    assert response.status_code == 503


# --- gating ----------------------------------------------------------------


def test_customer_report_404s_when_no_analysis_ever_ran(dpr_client, vendor):
    _paid_person(vendor)
    response = dpr_client.get("/api/v1/customer/services/GEO_CHECK/report", headers=ME)
    assert response.status_code == 404


def test_customer_report_locked_while_analysis_is_incomplete(
    dpr_client, dpr_pg, vendor
):
    _paid_person(vendor)
    sha = hashlib.sha256(b"doc").hexdigest()
    ref = f"wallet-person-custom-field:{PID}:GEO_CHECK"
    submit(
        dpr_pg,
        service_request_id=f"{PID}:GEO_CHECK",
        document_sha256=sha,
        source_reference=ref,
    )
    response = dpr_client.get("/api/v1/customer/services/GEO_CHECK/report", headers=ME)
    assert response.status_code == 403
    assert "REPORT_NOT_COMPLETED" in response.json()["detail"]["reasons"]


def test_customer_report_locked_when_payment_is_not_verified(
    dpr_client, dpr_pg, vendor
):
    """A stale 'Paid' at analysis time must not survive a fresh vendor check."""
    vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(
            200, json=_person(GeoCheck="Report ready", GeoCheck_Payment="NA")
        )
    )
    sha = hashlib.sha256(b"doc").hexdigest()
    ref = f"wallet-person-custom-field:{PID}:GEO_CHECK"
    _submit_completed_run(dpr_pg, source_reference=ref, sha=sha)
    response = dpr_client.get("/api/v1/customer/services/GEO_CHECK/report", headers=ME)
    assert response.status_code == 403
    assert "PAYMENT_NOT_VERIFIED" in response.json()["detail"]["reasons"]


def test_customer_report_releases_once_everything_checks_out(
    dpr_client, dpr_pg, vendor
):
    _paid_person(vendor)
    sha = hashlib.sha256(b"doc").hexdigest()
    ref = f"wallet-person-custom-field:{PID}:GEO_CHECK"
    _submit_completed_run(dpr_pg, source_reference=ref, sha=sha)
    response = dpr_client.get("/api/v1/customer/services/GEO_CHECK/report", headers=ME)
    assert response.status_code == 200
    body = response.json()
    assert body["document_sha256"] == sha
    assert body["status"] == "completed"


def test_customer_report_pdf_format_releases_a_real_pdf(dpr_client, dpr_pg, vendor):
    _paid_person(vendor)
    sha = hashlib.sha256(b"doc").hexdigest()
    ref = f"wallet-person-custom-field:{PID}:GEO_CHECK"
    _submit_completed_run(dpr_pg, source_reference=ref, sha=sha)
    response = dpr_client.get(
        "/api/v1/customer/services/GEO_CHECK/report?format=pdf", headers=ME
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content[:5] == b"%PDF-"


def test_customer_report_pdf_format_still_gated_like_json(dpr_client, vendor):
    """The PDF branch must reuse the same access_report() gate, not bypass it."""
    _paid_person(vendor)
    response = dpr_client.get(
        "/api/v1/customer/services/GEO_CHECK/report?format=pdf", headers=ME
    )
    assert response.status_code == 404


def test_customer_report_404s_for_an_unknown_service_code(dpr_client, vendor):
    _paid_person(vendor)
    response = dpr_client.get(
        "/api/v1/customer/services/NOT_A_SERVICE/report", headers=ME
    )
    assert response.status_code == 404


# --- BD mirror --------------------------------------------------------------


def test_bd_can_read_their_own_customers_report(dpr_client, dpr_pg, vendor):
    _paid_person(vendor)
    sha = hashlib.sha256(b"doc").hexdigest()
    ref = f"wallet-person-custom-field:{PID}:GEO_CHECK"
    _submit_completed_run(dpr_pg, source_reference=ref, sha=sha)
    response = dpr_client.get(
        f"/api/v1/bd/customers/{PID}/services/GEO_CHECK/report", headers=BD_AUTH
    )
    assert response.status_code == 200
    assert response.json()["document_sha256"] == sha


def test_bd_report_pdf_format_releases_a_real_pdf(dpr_client, dpr_pg, vendor):
    _paid_person(vendor)
    sha = hashlib.sha256(b"doc").hexdigest()
    ref = f"wallet-person-custom-field:{PID}:GEO_CHECK"
    _submit_completed_run(dpr_pg, source_reference=ref, sha=sha)
    response = dpr_client.get(
        f"/api/v1/bd/customers/{PID}/services/GEO_CHECK/report?format=pdf",
        headers=BD_AUTH,
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content[:5] == b"%PDF-"


def test_daily_submission_limit_rate_limits_further_uploads(dpr_pg, vendor):
    """The Wallet write must still succeed even once analysis is rate
    limited -- only queuing analysis is capped, never document storage."""
    settings = Settings(
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
        dpr_agentic_daily_submission_limit=2,
    )
    _paid_person(vendor, status="Opted")
    vendor.put(FIELDS).mock(return_value=httpx.Response(200, json=ack()))

    with TestClient(create_app(settings)) as client:
        for i in range(2):
            response = client.put(
                f"/api/v1/customer/services/GEO_CHECK/document-with-analysis?filename=r{i}.pdf",
                content=f"doc-{i}".encode(),
                headers={**ME, "Content-Type": "application/pdf"},
            )
            assert response.status_code == 202
            assert response.json()["analysis_status"] == "QUEUED"

        response = client.put(
            "/api/v1/customer/services/GEO_CHECK/document-with-analysis?filename=r3.pdf",
            content=b"doc-3-over-the-limit",
            headers={**ME, "Content-Type": "application/pdf"},
        )
        assert response.status_code == 202
        body = response.json()
        assert body["analysis_status"] == "RATE_LIMITED"
        assert body["wallet_storage_status"] == "STORED"
        assert body["run_id"] is None


def test_upload_queues_then_worker_completes_and_report_releases(
    dpr_client, dpr_pg, vendor, dpr_settings
):
    """End-to-end across the real async boundary: upload -> 202 + QUEUED
    (no inline analysis) -> /report still locked -> scripts/dpr_worker.py's
    own poll_once() claims and runs it for real (LangGraph, fake LLM only)
    -> /report releases the same report. Only inspect_document is stubbed
    (no real PDF parsing); nothing else is mocked.
    """
    _paid_person(vendor, status="Opted")  # opted + paid + not locked -> can_upload
    vendor.put(FIELDS).mock(return_value=httpx.Response(200, json=ack()))
    pdf_bytes = b"not a real pdf, inspect_document is stubbed"
    sha = hashlib.sha256(pdf_bytes).hexdigest()

    response = dpr_client.put(
        "/api/v1/customer/services/GEO_CHECK/document-with-analysis"
        "?filename=report.pdf",
        content=pdf_bytes,
        headers={**ME, "Content-Type": "application/pdf"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["wallet_storage_status"] == "STORED"
    assert body["analysis_status"] == "QUEUED"
    assert body["report_status"] == "NOT_PUBLISHED"
    run_id = body["run_id"]
    assert run_id is not None
    assert get_run(dpr_pg, run_id)["status"] == "QUEUED"

    # Analysis hasn't run yet -- no worker has touched it.
    pending = dpr_client.get("/api/v1/customer/services/GEO_CHECK/report", headers=ME)
    assert pending.status_code == 403
    assert pending.json()["detail"]["status"] == "QUEUED"

    def parsed():
        return SimpleNamespace(
            sha256=sha,
            mime="application/pdf",
            doc_class="DPR",
            confidence=0.99,
            needs_vision=False,
            pages=[{"page": 1, "text": "Detailed project report cost 100"}],
        )

    key = dpr_settings.dpr_staging_encryption_key.get_secret_value().encode()
    with patch.object(dpr_worker, "inspect_document", return_value=parsed()):
        claimed = asyncio.run(
            dpr_worker.poll_once(dpr_pg, key, FakeGemini(), dpr_settings)
        )
    assert claimed == 1
    assert get_run(dpr_pg, run_id)["status"] == "COMPLETED"

    report_response = dpr_client.get(
        "/api/v1/customer/services/GEO_CHECK/report", headers=ME
    )
    assert report_response.status_code == 200
    assert report_response.json()["document_sha256"] == sha
    # The worker deletes staged bytes once the run reaches a terminal state.
    assert dpr_worker.load_staged_upload(dpr_pg, run_id, key) is None


def test_bd_cannot_read_another_brokers_customer_report(dpr_client, dpr_pg, vendor):
    # "brokerId.id" is a top-level vendor field, not a custom field: it must
    # not go through the customFields-prefixing _person() helper.
    body = _person(GeoCheck="Report ready", GeoCheck_Payment="Paid")
    body["brokerId.id"] = "11111"
    vendor.post(BY_PERSON_ID).mock(return_value=httpx.Response(200, json=body))
    sha = hashlib.sha256(b"doc").hexdigest()
    ref = f"wallet-person-custom-field:{PID}:GEO_CHECK"
    _submit_completed_run(dpr_pg, source_reference=ref, sha=sha)
    response = dpr_client.get(
        f"/api/v1/bd/customers/{PID}/services/GEO_CHECK/report", headers=BD_AUTH
    )
    assert response.status_code == 404


# --- Section 15 audit: the remaining scenarios ------------------------------
#
# Covered already, above: unpaid (test_customer_report_locked_when_payment_is_
# not_verified), paid/released (test_customer_report_releases_once_everything_
# checks_out), wrong owner (test_bd_cannot_read_another_brokers_customer_
# report), a BD user (test_bd_can_read_their_own_customers_report), and no
# rerun on upload (test_upload_queues_then_worker_completes_and_report_
# releases, which checks GET returns 403/QUEUED before the worker ever runs).
# "No consent" has no live code path to exercise here by design (consent is
# enforced transiently at request/authorize time in enrolment.py, never
# persisted) -- it is covered directly on the pure policy function in
# tests/test_dpr_release_policy.py's consent_satisfied=False case.


def test_customer_report_401s_without_a_bearer_token(dpr_client, vendor):
    response = dpr_client.get("/api/v1/customer/services/GEO_CHECK/report")
    assert response.status_code == 401


def test_bd_report_401s_without_a_bearer_token(dpr_client, vendor):
    response = dpr_client.get(f"/api/v1/bd/customers/{PID}/services/GEO_CHECK/report")
    assert response.status_code == 401


def test_repeated_gets_never_create_or_change_a_run(dpr_client, dpr_pg, vendor):
    """Proof no GET starts the pipeline: polling the report endpoint, before
    and after the run completes, must never submit a new run or otherwise
    mutate the existing one."""
    _paid_person(vendor)
    sha = hashlib.sha256(b"doc").hexdigest()
    ref = f"wallet-person-custom-field:{PID}:GEO_CHECK"
    run_id, _ = submit(
        dpr_pg,
        service_request_id=f"{PID}:GEO_CHECK",
        document_sha256=sha,
        source_reference=ref,
    )

    def _run_count() -> int:
        from sqlalchemy import text

        with dpr_pg.connect() as conn:
            return conn.execute(
                text(
                    "SELECT count(*) FROM dpr_analysis_runs WHERE source_reference=:r"
                ),
                {"r": ref},
            ).scalar_one()

    assert _run_count() == 1
    for _ in range(5):
        response = dpr_client.get(
            "/api/v1/customer/services/GEO_CHECK/report", headers=ME
        )
        assert response.status_code == 403
        assert response.json()["detail"]["status"] == "QUEUED"
    assert _run_count() == 1
    assert get_run(dpr_pg, run_id)["status"] == "QUEUED"

    token = claim(dpr_pg, run_id)
    store_report(dpr_pg, run_id, _report(sha))
    finish(dpr_pg, run_id, token, success=True, artifact_ref=run_id)
    from app.services.dpr_agentic.branch_status import (
        init_branches,
        mark_report_completed,
        mark_wallet_stored,
    )

    init_branches(dpr_pg, run_id)
    mark_wallet_stored(dpr_pg, run_id, ref)
    mark_report_completed(dpr_pg, run_id, run_id)

    for _ in range(5):
        response = dpr_client.get(
            "/api/v1/customer/services/GEO_CHECK/report", headers=ME
        )
        assert response.status_code == 200
    assert _run_count() == 1
    assert list_claimable(dpr_pg) == []


def test_a_specific_older_version_is_still_retrievable(dpr_client, dpr_pg, vendor):
    """A DPR correction submits a new run (new document_sha256); the
    service's version history continues as v2, and the older version stays
    reachable by number."""
    _paid_person(vendor)
    ref = f"wallet-person-custom-field:{PID}:GEO_CHECK"
    sha1 = hashlib.sha256(b"doc-v1").hexdigest()
    _submit_completed_run(dpr_pg, source_reference=ref, sha=sha1)
    sha2 = hashlib.sha256(b"doc-v2-corrected").hexdigest()
    _submit_completed_run(dpr_pg, source_reference=ref, sha=sha2)
    assert list_report_versions(dpr_pg, ref) == [1, 2]

    latest = dpr_client.get("/api/v1/customer/services/GEO_CHECK/report", headers=ME)
    assert latest.status_code == 200
    assert latest.json()["document_sha256"] == sha2

    older = dpr_client.get(
        "/api/v1/customer/services/GEO_CHECK/report?version=1", headers=ME
    )
    assert older.status_code == 200
    assert older.json()["document_sha256"] == sha1

    older_pdf = dpr_client.get(
        "/api/v1/customer/services/GEO_CHECK/report?format=pdf&version=1", headers=ME
    )
    assert older_pdf.status_code == 200
    assert older_pdf.content[:5] == b"%PDF-"


def test_report_responses_are_marked_private_no_store(dpr_client, dpr_pg, vendor):
    _paid_person(vendor)
    sha = hashlib.sha256(b"doc").hexdigest()
    ref = f"wallet-person-custom-field:{PID}:GEO_CHECK"
    _submit_completed_run(dpr_pg, source_reference=ref, sha=sha)
    response = dpr_client.get("/api/v1/customer/services/GEO_CHECK/report", headers=ME)
    assert response.headers["cache-control"] == "private, no-store"

    pdf_response = dpr_client.get(
        "/api/v1/customer/services/GEO_CHECK/report?format=pdf", headers=ME
    )
    assert pdf_response.headers["cache-control"] == "private, no-store"


def test_report_findings_endpoint_returns_the_same_gated_findings(
    dpr_client, dpr_pg, vendor
):
    _paid_person(vendor)
    sha = hashlib.sha256(b"doc").hexdigest()
    ref = f"wallet-person-custom-field:{PID}:GEO_CHECK"
    _submit_completed_run(dpr_pg, source_reference=ref, sha=sha)
    report = dpr_client.get(
        "/api/v1/customer/services/GEO_CHECK/report", headers=ME
    ).json()
    findings = dpr_client.get(
        "/api/v1/customer/services/GEO_CHECK/report/findings", headers=ME
    )
    assert findings.status_code == 200
    assert findings.json() == report["findings"]


def test_report_findings_endpoint_is_gated_like_the_report(dpr_client, vendor):
    _paid_person(vendor)
    response = dpr_client.get(
        "/api/v1/customer/services/GEO_CHECK/report/findings", headers=ME
    )
    assert response.status_code == 404


def test_report_versions_endpoint_lists_every_version(dpr_client, dpr_pg, vendor):
    _paid_person(vendor)
    ref = f"wallet-person-custom-field:{PID}:GEO_CHECK"
    _submit_completed_run(
        dpr_pg, source_reference=ref, sha=hashlib.sha256(b"1").hexdigest()
    )
    _submit_completed_run(
        dpr_pg, source_reference=ref, sha=hashlib.sha256(b"2").hexdigest()
    )
    response = dpr_client.get(
        "/api/v1/customer/services/GEO_CHECK/report/versions", headers=ME
    )
    assert response.status_code == 200
    assert response.json() == [1, 2]


def test_report_annexures_endpoint_returns_all_seven_sections(
    dpr_client, dpr_pg, vendor
):
    """Section 12: Annexures A-G, each keyed by letter, each with an
    explicit `missing` list -- see docs/CAM_REPORT_SPEC.md and
    app/services/dpr_agentic/cam_annexures.py."""
    _paid_person(vendor)
    sha = hashlib.sha256(b"doc").hexdigest()
    ref = f"wallet-person-custom-field:{PID}:GEO_CHECK"
    _submit_completed_run(dpr_pg, source_reference=ref, sha=sha)
    response = dpr_client.get(
        "/api/v1/customer/services/GEO_CHECK/report/annexures", headers=ME
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "A_financial_statements",
        "B_repayment",
        "C_sensitivity_analysis",
        "D_project_feasibility",
        "E_findings_register",
        "F_sbi_and_scheme_references",
        "G_audit_trail",
    }
    for section in body.values():
        assert "missing" in section
    assert body["G_audit_trail"]["analysis_id"]
    assert body["G_audit_trail"]["report_version"] >= 1


def test_report_summary_endpoint_returns_readiness_and_counts(
    dpr_client, dpr_pg, vendor
):
    _paid_person(vendor)
    sha = hashlib.sha256(b"doc").hexdigest()
    ref = f"wallet-person-custom-field:{PID}:GEO_CHECK"
    _submit_completed_run(dpr_pg, source_reference=ref, sha=sha)
    response = dpr_client.get(
        "/api/v1/customer/services/GEO_CHECK/report/summary", headers=ME
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "version",
        "readiness_status",
        "must_fix_count",
        "should_fix_count",
        "good_to_fix_count",
        "critical_findings_count",
        "outstanding_clarifications_count",
        "evidence_references_count",
        "financial_overview",
    }
    assert body["readiness_status"] in {
        "READY_FOR_SUBMISSION_REVIEW",
        "CORRECTIONS_REQUIRED",
        "EXPERT_REVIEW_REQUIRED",
        "INSUFFICIENT_INFORMATION",
    }
    assert set(body["financial_overview"].keys()) == {
        "project_cost_total",
        "requested_loan",
        "promoter_contribution",
        "funding_gap",
        "average_dscr",
    }


def test_bd_report_findings_versions_annexures_and_summary_mirror_customer(
    dpr_client, dpr_pg, vendor
):
    _paid_person(vendor)
    sha = hashlib.sha256(b"doc").hexdigest()
    ref = f"wallet-person-custom-field:{PID}:GEO_CHECK"
    _submit_completed_run(dpr_pg, source_reference=ref, sha=sha)
    for suffix in ("findings", "versions", "annexures", "summary"):
        response = dpr_client.get(
            f"/api/v1/bd/customers/{PID}/services/GEO_CHECK/report/{suffix}",
            headers=BD_AUTH,
        )
        assert response.status_code == 200


# --- findings/annexures/versions: unpaid and wrong-owner, both routers ------
#
# "No consent" has no live code path on any of these routes either, for the
# same reason as the report route itself (see the comment above
# test_customer_report_401s_without_a_bearer_token): consent_satisfied is
# never independently False at this layer by construction. All four routes
# (report, findings, annexures, versions) share report_service._gate(), so
# tests/test_dpr_release_policy.py's consent_satisfied=False case already
# covers every one of them in a single place, not just the report route.

_SUB_ROUTES = ["findings", "annexures", "versions", "summary"]


def test_customer_sub_routes_locked_when_unpaid(dpr_client, dpr_pg, vendor):
    vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(
            200, json=_person(GeoCheck="Report ready", GeoCheck_Payment="NA")
        )
    )
    sha = hashlib.sha256(b"doc").hexdigest()
    ref = f"wallet-person-custom-field:{PID}:GEO_CHECK"
    _submit_completed_run(dpr_pg, source_reference=ref, sha=sha)
    for suffix in _SUB_ROUTES:
        response = dpr_client.get(
            f"/api/v1/customer/services/GEO_CHECK/report/{suffix}", headers=ME
        )
        assert response.status_code == 403, suffix
        assert "PAYMENT_NOT_VERIFIED" in response.json()["detail"]["reasons"]


def test_bd_sub_routes_locked_when_unpaid(dpr_client, dpr_pg, vendor):
    vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(
            200, json=_person(GeoCheck="Report ready", GeoCheck_Payment="NA")
        )
    )
    sha = hashlib.sha256(b"doc").hexdigest()
    ref = f"wallet-person-custom-field:{PID}:GEO_CHECK"
    _submit_completed_run(dpr_pg, source_reference=ref, sha=sha)
    for suffix in _SUB_ROUTES:
        response = dpr_client.get(
            f"/api/v1/bd/customers/{PID}/services/GEO_CHECK/report/{suffix}",
            headers=BD_AUTH,
        )
        assert response.status_code == 403, suffix
        assert "PAYMENT_NOT_VERIFIED" in response.json()["detail"]["reasons"]


def test_bd_sub_routes_404_for_another_brokers_customer(dpr_client, dpr_pg, vendor):
    """Customer routes have no "wrong owner" case to test: person_id is
    always the token's own `me`, never attacker-supplied, so there is no
    request shape that could even attempt to read someone else's report."""
    body = _person(GeoCheck="Report ready", GeoCheck_Payment="Paid")
    body["brokerId.id"] = "11111"
    vendor.post(BY_PERSON_ID).mock(return_value=httpx.Response(200, json=body))
    sha = hashlib.sha256(b"doc").hexdigest()
    ref = f"wallet-person-custom-field:{PID}:GEO_CHECK"
    _submit_completed_run(dpr_pg, source_reference=ref, sha=sha)
    for suffix in _SUB_ROUTES:
        response = dpr_client.get(
            f"/api/v1/bd/customers/{PID}/services/GEO_CHECK/report/{suffix}",
            headers=BD_AUTH,
        )
        assert response.status_code == 404, suffix
