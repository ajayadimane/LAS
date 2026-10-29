"""Opting into services, payment and documents, with the vendor mocked.

Each service has its own fields (F, F_Payment, F_PayMode, F_TxnId, F_Doc);
Payment_Status / Payment_Mode / Doc_Status are a summary recomputed on every
write. See app/services/service_fields.py.
"""

import base64
import json
import logging

import httpx
import pytest

from app.core.config import Settings
from app.services.service_fields import summary
from tests.conftest import ack, error_envelope
from tests.test_bd import AUTH, BY_PERSON_ID, REAL_PERSON

FIELDS = "/customermgmt/person/customFields"
TRANSFER = "/transfer/systemAccountPayment"
PID = "9000000013"
BASE = f"/api/v1/bd/customers/{PID}"

# Captured from the user's spec, 2026-10-27.
TRANSFER_ACK = {
    "transactionId": "7520896995384025088",
    "correlationId": "904db1ee-07d4-4ad2-96d9-e3e3b046b48b",
    "timeStamp": "2026-10-27T17:19:36.203256667Z",
    "data": None,
}

PDF = b"%PDF-1.4 a tiny test document"
DOC = json.dumps(
    {
        "name": "geo.pdf",
        "contentType": "application/pdf",
        "data": base64.b64encode(PDF).decode(),
    }
)

PAY_LATER = {
    "services": ["DPR_ANALYSIS", "GEO_CHECK"],
    "mode": "pay_later",
    "bd_consent": True,
    "customer_consent": True,
}


def _person(**fields: str) -> dict:
    return {**REAL_PERSON, **{f"customFields.{k}": v for k, v in fields.items()}}


def _bodies(route) -> list[dict]:
    return [json.loads(c.request.read()) for c in route.calls]


def _written(route, n: int = 0) -> dict:
    return _bodies(route)[n]["customFields"]


@pytest.fixture
def person(vendor):
    """The customer as the vendor returns them; tests set the custom fields."""

    def set_fields(**fields: str):
        return vendor.post(BY_PERSON_ID).mock(
            return_value=httpx.Response(200, json=_person(**fields))
        )

    set_fields()
    return set_fields


@pytest.fixture
def fields(vendor):
    return vendor.put(FIELDS).mock(return_value=httpx.Response(200, json=ack()))


@pytest.fixture
def transfer(vendor):
    return vendor.put(TRANSFER).mock(
        return_value=httpx.Response(200, json=TRANSFER_ACK)
    )


# --- catalogue -----------------------------------------------------------


def test_the_catalogue_comes_from_config_without_a_token(client, vendor):
    body = client.get("/api/v1/services").json()
    assert body["joining_fee"] == 295
    assert [s["code"] for s in body["services"]] == [
        "DPR_GENERATION",
        "DPR_ANALYSIS",
        "GEO_CHECK",
        "VALUE_ADDITION",
        "DOC_VERIFICATION",
    ]
    assert body["services"][3]["external"] is True
    assert not vendor.calls


# --- summary -------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({}, ("NA", "NA", "NA")),
        (
            {
                "GeoCheck": "Opted",
                "GeoCheck_Payment": "Paid",
                "GeoCheck_PayMode": "Pay now",
            },
            ("Paid", "Pay now", "NA"),
        ),
        (
            {
                "GeoCheck": "Docs uploaded",
                "GeoCheck_Payment": "Paid",
                "GeoCheck_PayMode": "Pay now",
                "GeoCheck_Doc": DOC,
                "DPR_Analysis": "Opted",
                "DPR_Analysis_Payment": "Processing",
                "DPR_Analysis_PayMode": "Pay later",
            },
            ("Processing", "Mixed", "Partially uploaded"),
        ),
        (
            {
                "GeoCheck": "Approved",
                "GeoCheck_Payment": "paid",
                "GeoCheck_PayMode": "Pay later",
                "GeoCheck_Doc": DOC,
                "DPR_Analysis": "NA",
                "DPR_Analysis_Payment": "NA",
            },
            ("Paid", "Pay later", "Uploaded"),
        ),
    ],
)
def test_the_summary_follows_the_services(values, expected):
    s = summary(values, Settings(_env_file=None))
    assert (s["Payment_Status"], s["Payment_Mode"], s["Doc_Status"]) == expected


# --- state ---------------------------------------------------------------


def test_a_new_customer_has_nothing_opted(client, person):
    body = client.get(f"{BASE}/services", headers=AUTH).json()
    assert [s["code"] for s in body["services"]] == [
        "DPR_GENERATION",
        "DPR_ANALYSIS",
        "GEO_CHECK",
        "DOC_VERIFICATION",
    ]  # the partner-portal service is not paid or documented here
    assert not any(s["opted"] for s in body["services"])
    assert body["first_payment"] is True
    assert body["payment_in_progress"] is False


def test_state_is_per_service(client, person):
    person(
        DPR_Analysis="Opted",
        DPR_Analysis_Payment="Paid",
        DPR_Analysis_PayMode="Pay now",
        GeoCheck="Docs uploaded",
        GeoCheck_Payment="Paid",
        GeoCheck_PayMode="Pay later",
        GeoCheck_TxnId="7520906027847049216",
        GeoCheck_Doc=DOC,
        Payment_Status="Paid",
        Payment_Mode="Mixed",
        Doc_Status="Partially uploaded",
    )
    body = client.get(f"{BASE}/services", headers=AUTH).json()
    by_code = {s["code"]: s for s in body["services"]}
    assert by_code["GEO_CHECK"] == {
        "code": "GEO_CHECK",
        "name": "Geo Check",
        "fee": 500,
        "opted": True,
        "requested": False,
        "status": "Docs uploaded",
        "payment_status": "Paid",
        "payment_mode": "Pay later",
        "transaction_id": "7520906027847049216",
        "document": {
            "name": "geo.pdf",
            "content_type": "application/pdf",
            "size_bytes": len(PDF),
        },
        "can_upload": True,
    }
    assert by_code["DPR_ANALYSIS"]["payment_mode"] == "Pay now"
    assert by_code["DPR_ANALYSIS"]["document"] is None
    assert by_code["DPR_GENERATION"]["opted"] is False
    assert by_code["DPR_GENERATION"]["can_upload"] is False
    assert body["first_payment"] is False
    assert body["payment_mode"] == "Mixed"
    # The document itself never appears in the state.
    assert base64.b64encode(PDF).decode() not in json.dumps(body)


# --- pay now -------------------------------------------------------------


def test_pay_now_records_each_service_and_the_summary(client, person, fields, transfer):
    response = client.post(
        f"{BASE}/payment",
        json={"services": ["GEO_CHECK", "DPR_ANALYSIS"], "mode": "pay_now"},
        headers=AUTH,
    )

    assert response.status_code == 200
    assert _bodies(fields) == [
        {
            "personId": PID,
            "programManagerId": "LAS",
            "customFields": {
                "GeoCheck": "Opted",
                "GeoCheck_Payment": "Paid",
                "GeoCheck_PayMode": "Pay now",
                "DPR_Analysis": "Opted",
                "DPR_Analysis_Payment": "Paid",
                "DPR_Analysis_PayMode": "Pay now",
                "Payment_Status": "Paid",
                "Payment_Mode": "Pay now",
                "Doc_Status": "NA",
            },
        }
    ]
    assert not transfer.called  # the customer paid by QR, outside the vendor
    body = response.json()
    assert (body["amount"], body["joining_fee"]) == (500 + 1000 + 295, 295)
    assert body["payment_status"] == "Paid"
    assert body["transfer"] is None


# --- pay later -----------------------------------------------------------


def test_pay_later_claims_then_transfers_then_marks_paid(
    client, person, fields, transfer
):
    order = []
    fields.side_effect = lambda r: (
        order.append("fields") or httpx.Response(200, json=ack())
    )
    transfer.side_effect = lambda r: (
        order.append("transfer") or httpx.Response(200, json=TRANSFER_ACK)
    )

    response = client.post(f"{BASE}/payment", json=PAY_LATER, headers=AUTH)

    assert response.status_code == 200
    assert order == ["fields", "transfer", "fields"]
    assert _written(fields, 0) == {
        "DPR_Analysis": "Opted",
        "DPR_Analysis_Payment": "Processing",
        "DPR_Analysis_PayMode": "Pay later",
        "GeoCheck": "Opted",
        "GeoCheck_Payment": "Processing",
        "GeoCheck_PayMode": "Pay later",
        "Payment_Status": "Processing",
        "Payment_Mode": "Pay later",
        "Doc_Status": "NA",
    }
    txn = "7520896995384025088"
    assert _written(fields, 1) == {
        "DPR_Analysis_Payment": "Paid",
        "DPR_Analysis_TxnId": txn,
        "GeoCheck_Payment": "Paid",
        "GeoCheck_TxnId": txn,
        "Payment_Status": "Paid",
        "Payment_Mode": "Pay later",
        "Doc_Status": "NA",
    }
    # The user's sample body, with the amount and location filled in.
    assert _bodies(transfer) == [
        {
            "programManagerId": "LAS",
            "transferTypeId": "pool_to_main",
            "debitFrom": "",
            "creditTo": "IntegraMain",
            "creditToTagId": "tag1",
            "creditToGroupId": "group1",
            "amount": 1000 + 500 + 295,
            "customFields": {},
            "location": {"latitude": 12.9716, "longitude": 77.5946},
        }
    ]
    assert transfer.calls.last.request.headers["authorization"] == AUTH["Authorization"]
    body = response.json()
    assert body["payment_status"] == "Paid"
    assert body["status_recorded"] is True
    assert body["transfer"]["transaction_id"] == txn


@pytest.mark.parametrize(
    "consent",
    [
        {"bd_consent": True, "customer_consent": False},
        {"bd_consent": False, "customer_consent": True},
        {},
    ],
)
def test_pay_later_needs_both_consents(client, vendor, consent):
    payload = {"services": ["GEO_CHECK"], "mode": "pay_later", **consent}
    response = client.post(f"{BASE}/payment", json=payload, headers=AUTH)
    assert response.status_code == 422
    assert not vendor.calls


def test_a_refused_transfer_releases_the_claim(client, vendor, person, fields):
    transfer = vendor.put(TRANSFER).mock(
        return_value=httpx.Response(400, json=error_envelope(400, "INSUFFICIENT_FUNDS"))
    )

    response = client.post(f"{BASE}/payment", json=PAY_LATER, headers=AUTH)

    assert response.status_code == 400
    assert _written(fields, 1) == {
        "DPR_Analysis": "NA",
        "DPR_Analysis_Payment": "NA",
        "DPR_Analysis_PayMode": "NA",
        "GeoCheck": "NA",
        "GeoCheck_Payment": "NA",
        "GeoCheck_PayMode": "NA",
        "Payment_Status": "NA",
        "Payment_Mode": "NA",
        "Doc_Status": "NA",
    }
    assert transfer.call_count == 1


def test_an_unknown_transfer_outcome_is_never_retried_or_released(
    client, vendor, person, fields
):
    transfer = vendor.put(TRANSFER).mock(
        return_value=httpx.Response(500, json=error_envelope(500))
    )

    response = client.post(f"{BASE}/payment", json=PAY_LATER, headers=AUTH)

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "PAYMENT_OUTCOME_UNKNOWN"
    assert "Do not try again" in error["message"]
    assert error["transaction_id"] == "7510946730856935424"
    assert transfer.call_count == 1
    # Only the claim: "Processing" stays, blocking another payment.
    assert len(fields.calls) == 1


def test_a_timed_out_transfer_is_an_unknown_outcome(client, vendor, person, fields):
    vendor.put(TRANSFER).mock(side_effect=httpx.ReadTimeout("slow"))
    response = client.post(f"{BASE}/payment", json=PAY_LATER, headers=AUTH)
    assert response.json()["error"]["code"] == "PAYMENT_OUTCOME_UNKNOWN"
    assert len(fields.calls) == 1


def test_a_401_on_the_transfer_does_not_sign_the_bd_out(client, vendor, person, fields):
    vendor.put(TRANSFER).mock(return_value=httpx.Response(401))
    response = client.post(f"{BASE}/payment", json=PAY_LATER, headers=AUTH)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "TRANSFER_NOT_PERMITTED"
    assert len(fields.calls) == 2  # claimed, then released


def test_money_moved_but_status_not_recorded_is_reported(
    client, vendor, person, transfer
):
    vendor.put(FIELDS).mock(
        side_effect=[
            httpx.Response(200, json=ack()),
            httpx.Response(500, json=error_envelope(500)),
        ]
    )
    response = client.post(f"{BASE}/payment", json=PAY_LATER, headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["status_recorded"] is False
    assert body["payment_status"] == "Processing"
    assert body["transfer"]["transaction_id"] == "7520896995384025088"


# --- refusals ------------------------------------------------------------


@pytest.mark.parametrize("value", ["Processing", "processing"])
def test_no_payment_while_one_is_in_progress(client, person, fields, transfer, value):
    person(DPR_Creation="Opted", DPR_Creation_Payment=value)
    response = client.post(f"{BASE}/payment", json=PAY_LATER, headers=AUTH)
    assert response.status_code == 409
    assert not fields.called
    assert not transfer.called


@pytest.mark.parametrize("code", ["VALUE_ADDITION", "NOPE"])
def test_only_in_house_services_can_be_paid_here(client, vendor, code):
    payload = {"services": [code], "mode": "pay_now"}
    response = client.post(f"{BASE}/payment", json=payload, headers=AUTH)
    assert response.status_code == 404
    assert not vendor.calls


def test_another_brokers_customer_cannot_be_charged(client, vendor, fields, transfer):
    vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(200, json={**REAL_PERSON, "brokerId.id": "11111"})
    )
    response = client.post(f"{BASE}/payment", json=PAY_LATER, headers=AUTH)
    assert response.status_code == 404
    assert not fields.called
    assert not transfer.called


def test_an_empty_service_list_is_rejected(client, vendor):
    response = client.post(
        f"{BASE}/payment", json={"services": [], "mode": "pay_now"}, headers=AUTH
    )
    assert response.status_code == 422
    assert not vendor.calls


# --- adding services later (Edit customer) ------------------------------

PAID_GEO = {
    "GeoCheck": "Docs uploaded",
    "GeoCheck_Payment": "Paid",
    "GeoCheck_PayMode": "Pay later",
    "GeoCheck_Doc": DOC,
    "Payment_Status": "Paid",
    "Payment_Mode": "Pay later",
    "Doc_Status": "Uploaded",
}


def test_a_paid_customer_can_add_a_service_without_the_joining_fee(
    client, person, fields, transfer
):
    person(**PAID_GEO)

    response = client.post(
        f"{BASE}/payment",
        json={"services": ["DPR_ANALYSIS"], "mode": "pay_now"},
        headers=AUTH,
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["amount"], body["joining_fee"]) == (1000, 0)
    assert _written(fields) == {
        "DPR_Analysis": "Opted",
        "DPR_Analysis_Payment": "Paid",
        "DPR_Analysis_PayMode": "Pay now",
        "Payment_Status": "Paid",
        # Geo Check went pay later, DPR Analysis pay now.
        "Payment_Mode": "Mixed",
        # Geo Check's document is in, DPR Analysis's is not.
        "Doc_Status": "Partially uploaded",
    }
    assert not transfer.called


def test_an_added_service_paid_later_transfers_only_its_fee(
    client, person, fields, transfer
):
    person(GeoCheck="Opted", GeoCheck_Payment="Paid", GeoCheck_PayMode="Pay now")
    payload = {**PAY_LATER, "services": ["DOC_VERIFICATION"]}

    response = client.post(f"{BASE}/payment", json=payload, headers=AUTH)

    assert response.status_code == 200
    assert _bodies(transfer)[0]["amount"] == 500


def test_a_service_already_opted_cannot_be_paid_again(client, person, fields, transfer):
    person(GeoCheck="Opted", GeoCheck_Payment="Paid")
    payload = {**PAY_LATER, "services": ["GEO_CHECK", "DPR_ANALYSIS"]}
    response = client.post(f"{BASE}/payment", json=payload, headers=AUTH)
    assert response.status_code == 409
    assert "Geo Check" in response.json()["detail"]
    assert not fields.called
    assert not transfer.called


def test_a_refused_added_payment_leaves_the_earlier_one_intact(
    client, vendor, person, fields
):
    person(**PAID_GEO)
    vendor.put(TRANSFER).mock(
        return_value=httpx.Response(400, json=error_envelope(400, "INSUFFICIENT_FUNDS"))
    )
    payload = {**PAY_LATER, "services": ["DPR_ANALYSIS"]}

    response = client.post(f"{BASE}/payment", json=payload, headers=AUTH)

    assert response.status_code == 400
    release = _written(fields, 1)
    assert release == {
        "DPR_Analysis": "NA",
        "DPR_Analysis_Payment": "NA",
        "DPR_Analysis_PayMode": "NA",
        "Payment_Status": "Paid",
        "Payment_Mode": "Pay later",
        "Doc_Status": "Uploaded",
    }
    assert not any(k.startswith("GeoCheck") for k in release)


# --- documents -----------------------------------------------------------


def _upload(client, code="GEO_CHECK", data=PDF, content_type="application/pdf"):
    return client.put(
        f"{BASE}/services/{code}/document?filename=geo%20check.pdf",
        content=data,
        headers={**AUTH, "Content-Type": content_type},
    )


PAID_TWO = {
    "GeoCheck": "Opted",
    "GeoCheck_Payment": "Paid",
    "GeoCheck_PayMode": "Pay now",
    "DPR_Analysis": "Opted",
    "DPR_Analysis_Payment": "Paid",
    "DPR_Analysis_PayMode": "Pay now",
}


def test_upload_stores_base64_in_the_doc_field(client, person, fields, caplog):
    person(**PAID_TWO)

    with caplog.at_level(logging.DEBUG):
        response = _upload(client)

    assert response.status_code == 200
    written = _written(fields)
    assert json.loads(written["GeoCheck_Doc"]) == {
        "name": "geo check.pdf",
        "contentType": "application/pdf",
        "data": base64.b64encode(PDF).decode(),
    }
    assert written["GeoCheck"] == "Docs uploaded"
    # DPR Analysis is opted but has no document yet.
    assert written["Doc_Status"] == "Partially uploaded"
    assert response.json()["document"]["size_bytes"] == len(PDF)
    assert base64.b64encode(PDF).decode() not in caplog.text


def test_the_last_document_completes_doc_status(client, person, fields):
    person(**PAID_TWO, DPR_Analysis_Doc=DOC)
    response = _upload(client)
    assert _written(fields)["Doc_Status"] == "Uploaded"
    assert response.json()["doc_status"] == "Uploaded"


def test_a_rejected_document_can_be_replaced(client, person, fields):
    person(**{**PAID_TWO, "GeoCheck": "Rejected", "GeoCheck_Doc": DOC})
    assert _upload(client).status_code == 200
    assert _written(fields)["GeoCheck"] == "Docs uploaded"


def test_an_approved_document_is_locked(client, person, fields):
    person(**{**PAID_TWO, "GeoCheck": "Approved", "GeoCheck_Doc": DOC})
    assert _upload(client).status_code == 409
    assert not fields.called


def test_no_upload_before_that_service_is_paid(client, person, fields):
    person(GeoCheck="Opted", GeoCheck_Payment="Processing")
    assert _upload(client).status_code == 409
    assert not fields.called


def test_no_upload_for_a_service_not_opted(client, person, fields):
    person(DPR_Analysis="Opted", DPR_Analysis_Payment="Paid")
    assert _upload(client, "GEO_CHECK").status_code == 409
    assert not fields.called


def test_upload_rejects_unsupported_types_before_any_vendor_call(client, vendor):
    response = _upload(client, content_type="text/html")
    assert response.status_code == 415
    assert not vendor.calls


def test_upload_rejects_an_oversized_file(client, vendor, settings):
    big = b"x" * (settings.document_max_bytes + 1)
    assert _upload(client, data=big).status_code == 413
    assert not vendor.calls


def test_download_returns_the_stored_file(client, person):
    person(**{**PAID_TWO, "GeoCheck_Doc": DOC})
    response = client.get(f"{BASE}/services/GEO_CHECK/document", headers=AUTH)
    assert response.status_code == 200
    assert response.content == PDF
    assert response.headers["content-type"] == "application/pdf"


def test_download_of_a_service_without_a_document_is_404(client, person):
    person(**PAID_TWO)
    response = client.get(f"{BASE}/services/GEO_CHECK/document", headers=AUTH)
    assert response.status_code == 404
