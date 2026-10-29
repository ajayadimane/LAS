"""Person endpoints, with the vendor mocked by respx."""

import json

import httpx
import pytest

from tests.conftest import ack, bd_jwt, error_envelope

REGISTER_PATH = "/customermgmt/person/"
BY_PERSON_ID = "/walletInfoRetrieval/customerMgmtService/getPersonDetailsByPersonId"
BY_BROKER_ID = "/walletInfoRetrieval/customerMgmtService/getPersonDetailsByBrokerId"
CUSTOM_FIELDS = "/walletInfoRetrieval/customerMgmtService/customFields"

BD_TOKEN = bd_jwt()
AUTH = {"Authorization": f"Bearer {BD_TOKEN}"}

VALID_PERSON = {
    "first_name": "Allu",
    "last_name": "Arjun",
    "email_address": "alluarjun@example.com",
    "address_street_address": "Allu arjun street address.",
    "address_city": "Hyderabad",
    "address_state_province": "Telangana",
    "address_postal_code": "562332",
    "address_country_code": "IN",
    "mobile_number": "9824561458",
    "mobile_country_code": "91",
    "settlement_account_no": "967710110006162",
    "settlement_account_ifsc_code": "ICIC0002468",
    "group_id": "Merchant",
    "custom_fields": {"pan": "ABCDE1234F"},
}

VENDOR_PERSON = {
    "personId": "88655489",
    "firstName": "Allu",
    "lastName": "Arjun",
    "programManagerId": "ibkart",
    "groupId": "Merchant",
    "mobileNumber": "9824561458",
    "emailAddress": "alluarjun@example.com",
    "cifNumber": "3131969d-b23f-4e0e-8a8d-3d39f62e726e",
}


def _sent(route):
    return json.loads(route.calls.last.request.read().decode())


@pytest.fixture(autouse=True)
def defined_fields(vendor):
    """Registration first reads the defined custom fields. Default: none, so
    the payload is exactly what the test supplied. Tests override `.mock`."""
    return vendor.post(CUSTOM_FIELDS).mock(return_value=httpx.Response(200, json=[]))


# --- register -----------------------------------------------------------


def test_register_fills_every_other_defined_field_with_empty(
    client, vendor, defined_fields
):
    """The vendor rejects a registration missing any defined custom field."""
    defined_fields.mock(
        return_value=httpx.Response(
            200,
            json=[
                {"customFieldId": "pan"},
                {"customFieldId": "GEO_CHECK"},
                {"customFieldId": "GEO_CHECK_STATUS"},
            ],
        )
    )
    route = vendor.post(REGISTER_PATH).mock(
        return_value=httpx.Response(201, json=ack())
    )
    client.post("/api/v1/persons", json=VALID_PERSON, headers=AUTH)

    assert _sent(route)["customFields"] == {
        "pan": "ABCDE1234F",
        "GEO_CHECK": "",
        "GEO_CHECK_STATUS": "",
    }
    assert _sent(defined_fields) == {"programManagerId": "LAS"}


def test_register_fails_without_writing_if_the_field_list_fails(
    client, vendor, defined_fields
):
    defined_fields.mock(return_value=httpx.Response(404, json=error_envelope()))
    route = vendor.post(REGISTER_PATH).mock(
        return_value=httpx.Response(201, json=ack())
    )
    response = client.post("/api/v1/persons", json=VALID_PERSON, headers=AUTH)
    assert response.status_code == 404
    assert not route.called


def test_register_person_succeeds(client, vendor):
    vendor.post(REGISTER_PATH).mock(return_value=httpx.Response(201, json=ack()))
    response = client.post("/api/v1/persons", json=VALID_PERSON, headers=AUTH)
    assert response.status_code == 201
    assert response.json()["transaction_id"] == "7345430166900109312"


def test_register_person_maps_our_names_to_the_vendors(client, vendor):
    route = vendor.post(REGISTER_PATH).mock(
        return_value=httpx.Response(201, json=ack())
    )
    client.post("/api/v1/persons", json=VALID_PERSON, headers=AUTH)

    assert _sent(route) == {
        "emailAddress": "alluarjun@example.com",
        "addressStreetAddress": "Allu arjun street address.",
        "addressCity": "Hyderabad",
        "addressStateProvince": "Telangana",
        "addressPostalCode": "562332",
        "addressCountryCode": "IN",
        "mobileNumber": "9824561458",
        "mobileCountryCode": "91",
        "firstName": "Allu",
        "settlementAccountNo": "967710110006162",
        "settlementAccountIfscCode": "ICIC0002468",
        "programManagerId": "LAS",
        "groupId": "Merchant",
        "customFields": {"pan": "ABCDE1234F"},
        "lastName": "Arjun",
    }


def test_register_omits_optional_fields_rather_than_sending_null(client, vendor):
    """personId, lastName and brokerId are optional; null is not the same as absent."""
    route = vendor.post(REGISTER_PATH).mock(
        return_value=httpx.Response(201, json=ack())
    )
    minimal = {k: v for k, v in VALID_PERSON.items() if k != "last_name"}
    client.post("/api/v1/persons", json=minimal, headers=AUTH)

    body = _sent(route)
    assert "personId" not in body
    assert "lastName" not in body
    assert "brokerId" not in body


def test_register_includes_optional_fields_when_supplied(client, vendor):
    route = vendor.post(REGISTER_PATH).mock(
        return_value=httpx.Response(201, json=ack())
    )
    payload = dict(VALID_PERSON, person_id="88655489", broker_id="7376183912")
    client.post("/api/v1/persons", json=payload, headers=AUTH)

    body = _sent(route)
    assert body["personId"] == "88655489"
    assert body["brokerId"] == "7376183912"


def test_register_echoes_the_person_id_back(client, vendor):
    vendor.post(REGISTER_PATH).mock(return_value=httpx.Response(201, json=ack()))
    payload = dict(VALID_PERSON, person_id="88655489")
    response = client.post("/api/v1/persons", json=payload, headers=AUTH)
    assert response.json()["person_id"] == "88655489"


def test_register_person_id_is_null_when_the_vendor_derives_it(client, vendor):
    """Omitted personId means the vendor uses the mobile number and never says so."""
    vendor.post(REGISTER_PATH).mock(return_value=httpx.Response(201, json=ack()))
    response = client.post("/api/v1/persons", json=VALID_PERSON, headers=AUTH)
    assert response.json()["person_id"] is None


def test_register_requires_a_bearer_token(client, vendor):
    response = client.post("/api/v1/persons", json=VALID_PERSON)
    assert response.status_code == 401
    assert not vendor.calls


def test_register_duplicate_maps_to_409(client, vendor):
    vendor.post(REGISTER_PATH).mock(
        return_value=httpx.Response(
            409, json=error_envelope(status=409, message="PERSON_ALREADY_REGISTERED")
        )
    )
    response = client.post("/api/v1/persons", json=VALID_PERSON, headers=AUTH)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PERSON_ALREADY_REGISTERED"


# --- request validation happens before any vendor call ------------------


def test_rejects_a_mobile_number_that_is_not_ten_digits(client, vendor):
    bad = dict(VALID_PERSON, mobile_number="98245")
    assert client.post("/api/v1/persons", json=bad, headers=AUTH).status_code == 422
    assert not vendor.calls


def test_rejects_a_country_code_that_is_not_two_characters(client, vendor):
    bad = dict(VALID_PERSON, address_country_code="IND")
    assert client.post("/api/v1/persons", json=bad, headers=AUTH).status_code == 422
    assert not vendor.calls


def test_rejects_an_ifsc_code_of_the_wrong_length(client, vendor):
    bad = dict(VALID_PERSON, settlement_account_ifsc_code="ICIC2468")
    assert client.post("/api/v1/persons", json=bad, headers=AUTH).status_code == 422
    assert not vendor.calls


def test_rejects_a_person_id_containing_spaces(client, vendor):
    bad = dict(VALID_PERSON, person_id="has a space")
    assert client.post("/api/v1/persons", json=bad, headers=AUTH).status_code == 422
    assert not vendor.calls


def test_rejects_a_postal_code_that_is_too_short(client, vendor):
    bad = dict(VALID_PERSON, address_postal_code="123")
    assert client.post("/api/v1/persons", json=bad, headers=AUTH).status_code == 422
    assert not vendor.calls


# --- lookups ------------------------------------------------------------


def test_get_person_maps_the_vendor_object(client, vendor):
    vendor.post(BY_PERSON_ID).mock(return_value=httpx.Response(200, json=VENDOR_PERSON))
    response = client.get("/api/v1/persons/88655489", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "person_id": "88655489",
        "first_name": "Allu",
        "last_name": "Arjun",
        "program_manager_id": "ibkart",
        "group_id": "Merchant",
        "mobile_number": "9824561458",
        "email_address": "alluarjun@example.com",
        "cif_number": "3131969d-b23f-4e0e-8a8d-3d39f62e726e",
        # The PDF sample carries none of these; the real flat body does.
        "broker_id": None,
        "mobile_country_code": None,
        "address": {
            "street_address": None,
            "city": None,
            "state_province": None,
            "postal_code": None,
            "country_code": None,
        },
        "status": None,
        "created_at": None,
    }


def test_get_person_reads_the_real_flattened_body(client, vendor):
    from tests.test_bd import REAL_PERSON

    vendor.post(BY_PERSON_ID).mock(return_value=httpx.Response(200, json=REAL_PERSON))
    body = client.get("/api/v1/persons/9000000013", headers=AUTH).json()
    assert body["broker_id"] == "97315"
    assert body["address"]["city"] == "banglore"
    assert body["status"] == "ACTIVE"
    assert "1234567890" not in str(body)


def test_get_person_sends_the_id_and_program_manager(client, vendor):
    route = vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(200, json=VENDOR_PERSON)
    )
    client.get("/api/v1/persons/88655489?program_manager_id=ibkart", headers=AUTH)
    assert _sent(route) == {"personId": "88655489", "programManagerId": "ibkart"}


def test_get_person_not_found_maps_to_404(client, vendor):
    vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(
            404, json=error_envelope(message="PERSON_NOT_FOUND")
        )
    )
    response = client.get("/api/v1/persons/nobody", headers=AUTH)
    assert response.status_code == 404


def test_get_person_requires_a_bearer_token(client, vendor):
    assert client.get("/api/v1/persons/88655489").status_code == 401
    assert not vendor.calls


def test_list_by_broker_returns_a_counted_list(client, vendor):
    vendor.post(BY_BROKER_ID).mock(
        return_value=httpx.Response(200, json=[VENDOR_PERSON, VENDOR_PERSON])
    )
    response = client.get("/api/v1/persons?broker_id=722616812", headers=AUTH)
    body = response.json()
    assert body["count"] == 2
    assert body["persons"][0]["person_id"] == "88655489"


def test_list_by_broker_handles_an_empty_array(client, vendor):
    vendor.post(BY_BROKER_ID).mock(return_value=httpx.Response(200, json=[]))
    response = client.get("/api/v1/persons?broker_id=nobody", headers=AUTH)
    assert response.json() == {"count": 0, "persons": []}


def test_list_requires_the_broker_id_query_parameter(client, vendor):
    """The vendor offers no other way to list persons, so it cannot be optional."""
    assert client.get("/api/v1/persons", headers=AUTH).status_code == 422
    assert not vendor.calls


# --- retry policy, end to end ------------------------------------------


def test_person_reads_are_retried(client, vendor):
    """Reads live under /walletInfoRetrieval, so a 5xx is worth repeating."""
    route = vendor.post(BY_PERSON_ID).mock(
        side_effect=[
            httpx.Response(500, json=error_envelope(status=500)),
            httpx.Response(200, json=VENDOR_PERSON),
        ]
    )
    response = client.get("/api/v1/persons/88655489", headers=AUTH)
    assert response.status_code == 200
    assert route.call_count == 2


def test_person_registration_is_never_retried(client, vendor):
    """A write with no idempotency key must not be repeated."""
    route = vendor.post(REGISTER_PATH).mock(
        return_value=httpx.Response(500, json=error_envelope(status=500))
    )
    response = client.post("/api/v1/persons", json=VALID_PERSON, headers=AUTH)
    assert response.status_code == 502
    assert route.call_count == 1


def test_no_raw_vendor_field_names_reach_the_dashboard(client, vendor):
    vendor.post(BY_PERSON_ID).mock(return_value=httpx.Response(200, json=VENDOR_PERSON))
    text = client.get("/api/v1/persons/88655489", headers=AUTH).text
    for vendor_name in ("personId", "cifNumber", "programManagerId", "emailAddress"):
        assert vendor_name not in text
