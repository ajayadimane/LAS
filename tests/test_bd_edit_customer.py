"""Editing a customer's details: email and address, one vendor call each."""

import json

import httpx
import pytest

from tests.conftest import ack, error_envelope
from tests.test_bd import AUTH, BY_PERSON_ID, REAL_PERSON

EMAIL = "/customermgmt/person/emailAddress"
ADDRESS = "/customermgmt/person/address"
URL = "/api/v1/bd/customers/9000000013"

NEW_ADDRESS = {
    "street_address": "12 MG Road",
    "city": "Bengaluru",
    "state_province": "Karnataka",
    "postal_code": "560001",
    "country_code": "in",
}


@pytest.fixture
def owned(vendor):
    return vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(200, json=REAL_PERSON)
    )


def _body(route) -> dict:
    return json.loads(route.calls.last.request.read())


def test_changes_the_email(client, vendor, owned):
    route = vendor.put(EMAIL).mock(return_value=httpx.Response(200, json=ack()))

    response = client.put(
        f"{URL}/email", json={"email_address": "new@example.com"}, headers=AUTH
    )

    assert response.status_code == 200
    assert response.json()["updated"]["transaction_id"] == "7345430166900109312"
    assert _body(route) == {
        "programManagerId": "LAS",
        "personId": "9000000013",
        "emailAddress": "new@example.com",
    }


def test_replaces_the_whole_address(client, vendor, owned):
    route = vendor.put(ADDRESS).mock(return_value=httpx.Response(200, json=ack()))

    response = client.put(f"{URL}/address", json=NEW_ADDRESS, headers=AUTH)

    assert response.status_code == 200
    assert _body(route) == {
        "programManagerId": "LAS",
        "personId": "9000000013",
        "addressStreetAddress": "12 MG Road",
        "addressCity": "Bengaluru",
        "addressStateProvince": "Karnataka",
        "addressPostalCode": "560001",
        "addressCountryCode": "IN",
    }


def test_another_brokers_customer_is_not_changed(client, vendor):
    vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(200, json={**REAL_PERSON, "brokerId.id": "11111"})
    )
    email = vendor.put(EMAIL).mock(return_value=httpx.Response(200, json=ack()))
    address = vendor.put(ADDRESS).mock(return_value=httpx.Response(200, json=ack()))

    assert (
        client.put(
            f"{URL}/email", json={"email_address": "a@b.co"}, headers=AUTH
        ).status_code
        == 404
    )
    assert (
        client.put(f"{URL}/address", json=NEW_ADDRESS, headers=AUTH).status_code == 404
    )
    assert not email.called and not address.called


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("email", {"email_address": "not-an-email"}),
        ("address", {**NEW_ADDRESS, "postal_code": "12"}),
        ("address", {k: v for k, v in NEW_ADDRESS.items() if k != "city"}),
    ],
)
def test_invalid_input_never_reaches_the_vendor(client, vendor, path, body):
    response = client.put(f"{URL}/{path}", json=body, headers=AUTH)
    assert response.status_code == 422
    assert not vendor.calls


def test_a_vendor_rejection_is_passed_on(client, vendor, owned):
    vendor.put(EMAIL).mock(
        return_value=httpx.Response(
            400, json=error_envelope(status=400, message="INVALID_EMAIL")
        )
    )
    response = client.put(
        f"{URL}/email", json={"email_address": "x@y.co"}, headers=AUTH
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_EMAIL"


def test_updates_are_never_retried(client, vendor, owned):
    route = vendor.put(ADDRESS).mock(return_value=httpx.Response(503))
    client.put(f"{URL}/address", json=NEW_ADDRESS, headers=AUTH)
    assert route.call_count == 1


def test_a_customer_token_cannot_edit(client, vendor):
    from tests.conftest import customer_jwt

    headers = {"Authorization": f"Bearer {customer_jwt('9000000013')}"}
    response = client.put(
        f"{URL}/email", json={"email_address": "a@b.co"}, headers=headers
    )
    assert response.status_code == 403
    assert not vendor.calls
