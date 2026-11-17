# ruff: noqa: F811 (fixtures imported from test_dpr_report_access)
"""Pay later: the customer still owes the amount, so the service's report is
locked for the customer AND the BD until the BD confirms the customer paid
(user, 2026-11-16). app/services/dues.py, report_service._gate."""

import hashlib

import httpx
import pytest
from sqlalchemy import text

from app.services.dpr_agentic.report_access import (
    ProcessingStatus,
    ReportGateInput,
    RetentionStatus,
    StorageStatus,
    evaluate_report_access,
)
from app.services.dues import dues_for, is_settled, record_dues, settle
from tests.conftest import ack
from tests.test_bd import BY_PERSON_ID, REAL_PERSON
from tests.test_dpr_report_access import (  # noqa: F401 - fixtures
    BD_AUTH,
    ME,
    PID,
    _submit_completed_run,
    dpr_client,
    dpr_settings,
)
from tests.test_enrolment import FIELDS, TRANSFER, TRANSFER_ACK

REF = f"wallet-person-custom-field:{PID}:GEO_CHECK"
REPORT = "/api/v1/customer/services/GEO_CHECK/report"
BD_REPORT = f"/api/v1/bd/customers/{PID}/services/GEO_CHECK/report"


def _person(vendor, **fields: str):
    return vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(
            200,
            json={**REAL_PERSON, **{f"customFields.{k}": v for k, v in fields.items()}},
        )
    )


def _pay_later_customer(vendor):
    """GEO_CHECK paid by the pool (Pay later), report ready."""
    return _person(
        vendor,
        GeoCheck="Report ready",
        GeoCheck_Payment="Paid",
        GeoCheck_PayMode="Pay later",
    )


@pytest.fixture
def run(dpr_pg):
    with dpr_pg.begin() as conn:
        conn.execute(text("TRUNCATE pay_later_dues"))
    return _submit_completed_run(
        dpr_pg, source_reference=REF, sha=hashlib.sha256(b"doc").hexdigest()
    )


# --- the gate ---------------------------------------------------------------------


def test_the_gate_names_the_customer_payment_due():
    decision = evaluate_report_access(
        ReportGateInput(
            analysis_status=ProcessingStatus.COMPLETED,
            report_status=ProcessingStatus.COMPLETED,
            wallet_storage_status=StorageStatus.STORED,
            identity_verified=True,
            authenticated_owner=True,
            payment_verified=True,
            consent_satisfied=True,
            retention_status=RetentionStatus.COMPLIANT,
            report_artifact_verified=True,
            customer_payment_due=True,
        )
    )
    assert not decision.can_release
    assert decision.reasons == ("CUSTOMER_PAYMENT_DUE",)


# --- locked for the customer and the BD until confirmed -------------------------------


def test_a_pay_later_report_is_locked_for_customer_and_bd(dpr_client, run, vendor):
    _pay_later_customer(vendor)
    for url, auth in ((REPORT, ME), (BD_REPORT, BD_AUTH), (REPORT + "?format=pdf", ME)):
        response = dpr_client.get(url, headers=auth)
        assert response.status_code == 403, url
        assert "CUSTOMER_PAYMENT_DUE" in response.json()["detail"]["reasons"]


def test_a_pay_now_report_is_not_locked(dpr_client, run, vendor):
    _person(
        vendor,
        GeoCheck="Report ready",
        GeoCheck_Payment="Paid",
        GeoCheck_PayMode="Pay now",
    )
    assert dpr_client.get(REPORT, headers=ME).status_code == 200


def test_both_sides_see_what_is_owed(dpr_client, run, vendor):
    _pay_later_customer(vendor)
    mine = dpr_client.get("/api/v1/customer/dues", headers=ME).json()
    # No recorded due (paid before dues existed): the service's fee.
    assert mine["dues"] == [{"code": "GEO_CHECK", "name": "Geo Check", "amount": 500}]
    assert mine["total"] == 500
    bd = dpr_client.get(f"/api/v1/bd/customers/{PID}/dues", headers=BD_AUTH).json()
    assert bd["total"] == 500


def test_the_bd_confirming_payment_unlocks_the_report(dpr_client, run, vendor, dpr_pg):
    _pay_later_customer(vendor)
    response = dpr_client.post(
        f"/api/v1/bd/customers/{PID}/dues/confirm", json={}, headers=BD_AUTH
    )
    assert response.status_code == 200
    assert response.json()["settled"] == ["GEO_CHECK"]
    assert is_settled(dpr_pg, PID, "GEO_CHECK")
    assert dpr_client.get(REPORT, headers=ME).status_code == 200
    assert dpr_client.get(BD_REPORT, headers=BD_AUTH).status_code == 200
    assert dpr_client.get("/api/v1/customer/dues", headers=ME).json()["total"] == 0
    # Confirming again changes nothing.
    again = dpr_client.post(
        f"/api/v1/bd/customers/{PID}/dues/confirm", json={}, headers=BD_AUTH
    )
    assert again.json()["settled"] == []


def test_confirming_a_service_that_is_not_owed_is_refused(dpr_client, run, vendor):
    _pay_later_customer(vendor)
    response = dpr_client.post(
        f"/api/v1/bd/customers/{PID}/dues/confirm",
        json={"services": ["DPR_ANALYSIS"]},
        headers=BD_AUTH,
    )
    assert response.status_code == 409


def test_another_bds_customer_cannot_be_confirmed(dpr_client, run, vendor):
    vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(
            200,
            json={
                **REAL_PERSON,
                "brokerId.id": "11111",
                "customFields.GeoCheck_PayMode": "Pay later",
            },
        )
    )
    response = dpr_client.post(
        f"/api/v1/bd/customers/{PID}/dues/confirm", json={}, headers=BD_AUTH
    )
    assert response.status_code == 404


# --- a Pay later payment records what is owed -------------------------------------


def test_a_pay_later_payment_records_the_due_with_the_joining_fee(
    dpr_client, dpr_pg, vendor, run
):
    _person(vendor)  # nothing paid yet: first payment, joining fee due
    vendor.put(FIELDS).mock(return_value=httpx.Response(200, json=ack()))
    vendor.put(TRANSFER).mock(return_value=httpx.Response(200, json=TRANSFER_ACK))
    response = dpr_client.post(
        f"/api/v1/bd/customers/{PID}/payment",
        json={
            "services": ["GEO_CHECK"],
            "mode": "pay_later",
            "bd_consent": True,
            "customer_consent": True,
        },
        headers=BD_AUTH,
    )
    assert response.status_code == 200, response.text
    (due,) = dues_for(dpr_pg, PID)
    assert (due.service_code, due.amount, due.status) == ("GEO_CHECK", 795, "DUE")


# --- the store ----------------------------------------------------------------------


def test_a_settled_due_stays_settled(dpr_pg, run):
    record_dues(dpr_pg, PID, {"GEO_CHECK": 500}, "txn-1")
    assert settle(dpr_pg, PID, ["GEO_CHECK"], "97315") == ["GEO_CHECK"]
    record_dues(dpr_pg, PID, {"GEO_CHECK": 500}, "txn-2")  # never re-opened
    assert is_settled(dpr_pg, PID, "GEO_CHECK")
