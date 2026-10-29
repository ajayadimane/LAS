"""A customer opts in themselves; their BD authorizes (stands guarantor) or
declines. Vendor mocked with respx."""

import json

import httpx
import pytest

from app.main import create_app
from tests.conftest import ack, customer_jwt, error_envelope
from tests.test_bd import AUTH as BD_AUTH
from tests.test_bd import BY_BROKER_ID, BY_PERSON_ID, REAL_PERSON
from tests.test_enrolment import FIELDS, TRANSFER, TRANSFER_ACK

PID = "9000000013"
ME = {"Authorization": f"Bearer {customer_jwt(PID)}"}
BD_BASE = f"/api/v1/bd/customers/{PID}"


def _person(**fields: str) -> dict:
    return {**REAL_PERSON, **{f"customFields.{k}": v for k, v in fields.items()}}


def _written(route, n: int = 0) -> dict:
    return json.loads(route.calls[n].request.read())["customFields"]


@pytest.fixture
def person(vendor):
    def set_fields(**fields: str):
        return vendor.post(BY_PERSON_ID).mock(
            return_value=httpx.Response(200, json=_person(**fields))
        )

    return set_fields(), set_fields


@pytest.fixture
def fields(vendor):
    return vendor.put(FIELDS).mock(return_value=httpx.Response(200, json=ack()))


@pytest.fixture
def transfer(vendor):
    return vendor.put(TRANSFER).mock(
        return_value=httpx.Response(200, json=TRANSFER_ACK)
    )


def _set(person, **fields):
    return person[1](**fields)


# --- the customer's request ----------------------------------------------


def test_pay_later_request_waits_for_the_bd(client, person, fields, transfer):
    response = client.post(
        "/api/v1/customer/services/request",
        json={"services": ["GEO_CHECK"], "mode": "pay_later", "customer_consent": True},
        headers=ME,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["awaiting_bd"] is True
    assert body["amount"] == 500 + 295
    assert _written(fields) == {
        "GeoCheck": "Awaiting BD",
        "GeoCheck_PayMode": "Pay later",
        # A request is not opted: nothing to summarise yet.
        "Payment_Status": "NA",
        "Payment_Mode": "NA",
        "Doc_Status": "NA",
    }
    assert not transfer.called
    # The customer's own record, looked up by their own id.
    lookup = json.loads(person[0].calls.last.request.read())
    assert lookup["personId"] == PID


def test_pay_later_request_needs_the_customers_consent(client, vendor):
    response = client.post(
        "/api/v1/customer/services/request",
        json={"services": ["GEO_CHECK"], "mode": "pay_later"},
        headers=ME,
    )
    assert response.status_code == 422
    assert not vendor.calls


def test_pay_now_waits_for_the_bd_while_the_qr_is_a_placeholder(
    client, person, fields, transfer
):
    response = client.post(
        "/api/v1/customer/services/request",
        json={"services": ["GEO_CHECK"], "mode": "pay_now"},
        headers=ME,
    )
    assert response.json()["awaiting_bd"] is True
    assert _written(fields)["GeoCheck"] == "Awaiting BD"
    assert _written(fields)["GeoCheck_PayMode"] == "Pay now"


def test_pay_now_is_immediate_once_payments_can_be_confirmed(
    settings, vendor, person, fields, transfer
):
    from fastapi.testclient import TestClient

    settings.customer_pay_now_needs_confirmation = False
    with TestClient(create_app(settings)) as c:
        response = c.post(
            "/api/v1/customer/services/request",
            json={"services": ["GEO_CHECK"], "mode": "pay_now"},
            headers=ME,
        )
    body = response.json()
    assert body["awaiting_bd"] is False
    assert body["payment"]["payment_status"] == "Paid"
    assert _written(fields)["GeoCheck_Payment"] == "Paid"
    assert not transfer.called


@pytest.mark.parametrize("status_value", ["Opted", "Awaiting BD"])
def test_a_customer_cannot_request_what_is_taken_or_waiting(
    client, person, fields, status_value
):
    _set(person, GeoCheck=status_value)
    response = client.post(
        "/api/v1/customer/services/request",
        json={"services": ["GEO_CHECK"], "mode": "pay_later", "customer_consent": True},
        headers=ME,
    )
    assert response.status_code == 409
    assert not fields.called


def test_the_customer_sees_their_requests(client, person):
    _set(person, GeoCheck="Awaiting BD", GeoCheck_PayMode="Pay later")
    body = client.get("/api/v1/customer/services", headers=ME).json()
    geo = next(s for s in body["services"] if s["code"] == "GEO_CHECK")
    assert (geo["requested"], geo["opted"], geo["can_upload"]) == (True, False, False)


# --- the BD's side -------------------------------------------------------


def test_the_bd_list_shows_requests_separately(client, vendor):
    vendor.post(BY_BROKER_ID).mock(
        return_value=httpx.Response(
            200,
            json=[_person(GeoCheck="Awaiting BD", DPR_Analysis="Opted")],
        )
    )
    customer = client.get("/api/v1/bd/customers", headers=BD_AUTH).json()["customers"][
        0
    ]
    assert [s["code"] for s in customer["services"]] == ["DPR_ANALYSIS"]
    assert [s["code"] for s in customer["requests"]] == ["GEO_CHECK"]


def test_authorizing_pay_later_needs_the_guarantee(client, person, fields, transfer):
    _set(person, GeoCheck="Awaiting BD", GeoCheck_PayMode="Pay later")
    response = client.post(
        f"{BD_BASE}/services/GEO_CHECK/authorize", json={}, headers=BD_AUTH
    )
    assert response.status_code == 422
    assert not fields.called
    assert not transfer.called


def test_authorizing_pay_later_runs_the_transfer(client, person, fields, transfer):
    _set(person, GeoCheck="Awaiting BD", GeoCheck_PayMode="Pay later")

    response = client.post(
        f"{BD_BASE}/services/GEO_CHECK/authorize",
        json={"bd_consent": True},
        headers=BD_AUTH,
    )

    assert response.status_code == 200
    assert json.loads(transfer.calls.last.request.read())["amount"] == 500 + 295
    claim, done = _written(fields, 0), _written(fields, 1)
    assert (claim["GeoCheck"], claim["GeoCheck_Payment"]) == ("Opted", "Processing")
    assert done["GeoCheck_Payment"] == "Paid"
    assert done["GeoCheck_TxnId"] == "7520896995384025088"
    assert response.json()["mode"] == "pay_later"


def test_a_refused_transfer_puts_the_request_back(client, vendor, person, fields):
    _set(person, GeoCheck="Awaiting BD", GeoCheck_PayMode="Pay later")
    vendor.put(TRANSFER).mock(
        return_value=httpx.Response(400, json=error_envelope(400, "INSUFFICIENT_FUNDS"))
    )
    response = client.post(
        f"{BD_BASE}/services/GEO_CHECK/authorize",
        json={"bd_consent": True},
        headers=BD_AUTH,
    )
    assert response.status_code == 400
    release = _written(fields, 1)
    assert release["GeoCheck"] == "Awaiting BD"  # still waiting, not lost
    assert release["GeoCheck_PayMode"] == "Pay later"
    assert release["GeoCheck_Payment"] == "NA"


def test_authorizing_pay_now_confirms_receipt_without_a_transfer(
    client, person, fields, transfer
):
    _set(person, GeoCheck="Awaiting BD", GeoCheck_PayMode="Pay now")
    response = client.post(
        f"{BD_BASE}/services/GEO_CHECK/authorize", json={}, headers=BD_AUTH
    )
    assert response.status_code == 200
    assert _written(fields)["GeoCheck_Payment"] == "Paid"
    assert not transfer.called


def test_declining_frees_the_service(client, person, fields, transfer):
    _set(person, GeoCheck="Awaiting BD", GeoCheck_PayMode="Pay later")
    response = client.post(f"{BD_BASE}/services/GEO_CHECK/decline", headers=BD_AUTH)
    assert response.status_code == 200
    written = _written(fields)
    assert (written["GeoCheck"], written["GeoCheck_PayMode"]) == ("NA", "NA")
    assert not transfer.called


def test_only_a_waiting_request_can_be_authorized(client, person, fields):
    _set(person, GeoCheck="Opted", GeoCheck_Payment="Paid")
    response = client.post(
        f"{BD_BASE}/services/GEO_CHECK/authorize",
        json={"bd_consent": True},
        headers=BD_AUTH,
    )
    assert response.status_code == 409
    assert not fields.called


def test_the_bd_cannot_pay_directly_over_a_customers_request(client, person, fields):
    _set(person, GeoCheck="Awaiting BD", GeoCheck_PayMode="Pay later")
    response = client.post(
        f"{BD_BASE}/payment",
        json={"services": ["GEO_CHECK"], "mode": "pay_now"},
        headers=BD_AUTH,
    )
    assert response.status_code == 409
    assert "authorize" in response.json()["detail"]


def test_another_brokers_customer_cannot_be_authorized(client, vendor, transfer):
    vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(
            200,
            json={**_person(GeoCheck="Awaiting BD"), "brokerId.id": "11111"},
        )
    )
    response = client.post(
        f"{BD_BASE}/services/GEO_CHECK/authorize",
        json={"bd_consent": True},
        headers=BD_AUTH,
    )
    assert response.status_code == 404
    assert not transfer.called


# --- the customer's documents --------------------------------------------


def test_the_customer_uploads_their_own_document(client, person, fields):
    _set(person, GeoCheck="Opted", GeoCheck_Payment="Paid", GeoCheck_PayMode="Pay now")
    response = client.put(
        "/api/v1/customer/services/GEO_CHECK/document?filename=geo.pdf",
        content=b"%PDF-1.4 mine",
        headers={**ME, "Content-Type": "application/pdf"},
    )
    assert response.status_code == 200
    assert _written(fields)["GeoCheck"] == "Docs uploaded"


# --- profile -------------------------------------------------------------


def test_the_customer_sees_their_own_profile_and_nothing_sensitive(client, person):
    response = client.get("/api/v1/customer/profile", headers=ME)
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "person_id": PID,
        "first_name": "Kunal",
        "last_name": "B",
        "mobile_number": "9000000013",
        "mobile_country_code": "91",
        "email_address": "email@nomail.com",
        "address": {
            "street_address": "NO 06 yashod nagar",
            "city": "banglore",
            "state_province": "karnataka",
            "postal_code": "560091",
            "country_code": "IN",
        },
        "broker_id": "97315",
    }
    for secret in ("1234567890", "ICIC", "cif", "searchKey", "customFields"):
        assert secret not in response.text
