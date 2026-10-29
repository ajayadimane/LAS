"""BD dashboard: the customer list, with the vendor mocked by respx."""

import json

import httpx

from tests.conftest import bd_jwt, error_envelope, make_jwt

BY_BROKER_ID = "/walletInfoRetrieval/customerMgmtService/getPersonDetailsByBrokerId"
BD_ID = "97315"
AUTH = {"Authorization": f"Bearer {bd_jwt(BD_ID)}"}

# Captured from the live sandbox on 2026-10-27 (getPersonDetailsByBrokerId).
# The settlement account is a test value, but must still never be returned.
REAL_PERSON = {
    "customFields.DPR_Analysis": "NA",
    "searchKeyValues.SK2": "NA",
    "searchKeyValues.SK1": "NA",
    "lastName": "B",
    "searchKeyValues.SK4": "NA",
    "searchKeyValues.SK3": "NA",
    "mobileNumber": "9000000013",
    "groupId": "group1",
    "customFields.Doc_Status": "NA",
    "currentStatus.personStatus": "ACTIVE",
    "contactInformation.postalAddress.postalCode": "560091",
    "customFields.Payment_Status": "NA",
    "customFields.Doc_Verification": "NA",
    "searchKeyValues.SK6": "na",
    "contactInformation.postalAddress.stateProvince": "karnataka",
    "searchKeyValues.SK5": "NA",
    "searchKeyValues.SK8": "NA",
    "searchKeyValues.SK7": "NA",
    "createdAt": "2026-10-27T09:17:07.478210150Z",
    "emailAddress": "email@nomail.com",
    "contactInformation.postalAddress.city": "banglore",
    "customFields.Payment_Mode": "NA",
    "cifNumber": "adab7fae-efa5-45b7-8ab5-95a1960cc6b9",
    "programManagerId": "LAS",
    "settlementAccount.accountNumber": "1234567890",
    "customFields.GeoCheck": "NA",
    "brokerId.id": "97315",
    "firstName": "Kunal",
    "settlementAccount.ifscCode": "ICIC0001010",
    "contactInformation.postalAddress.countryCode": "IN",
    "customFields.DPR_Creation": "NA",
    "contactInformation.mobileNumber.countryCode": "91",
    "personId": "9000000013",
    "contactInformation.postalAddress.streetAddress": "NO 06 yashod nagar",
}


def _person(**fields) -> dict:
    return {**REAL_PERSON, **fields}


def test_lists_the_customers_of_the_signed_in_bd(client, vendor):
    route = vendor.post(BY_BROKER_ID).mock(
        return_value=httpx.Response(200, json=[REAL_PERSON])
    )

    response = client.get("/api/v1/bd/customers", headers=AUTH)

    assert response.status_code == 200
    assert json.loads(route.calls.last.request.read()) == {
        "brokerId": BD_ID,
        "programManagerId": "LAS",
    }
    body = response.json()
    assert body["broker_id"] == BD_ID
    assert body["count"] == 1
    assert body["customers"][0] == {
        "person_id": "9000000013",
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
        "status": "ACTIVE",
        "created_at": "2026-10-27T09:17:07.478210150Z",
        # Every field is "NA": nothing opted, no status set.
        "services": [],
        "requests": [],
        "doc_status": None,
        "payment_status": None,
        "payment_mode": None,
    }


def test_never_returns_the_settlement_account_or_search_keys(client, vendor):
    vendor.post(BY_BROKER_ID).mock(return_value=httpx.Response(200, json=[REAL_PERSON]))
    text = client.get("/api/v1/bd/customers", headers=AUTH).text
    assert "1234567890" not in text
    assert "ICIC0001010" not in text
    assert "SK1" not in text
    assert "cif" not in text.lower()


def test_opted_services_and_statuses_are_read_from_custom_fields(client, vendor):
    big_document = "JVBERi0xLjQK" * 1000  # a base64 PDF, as a value might hold
    vendor.post(BY_BROKER_ID).mock(
        return_value=httpx.Response(
            200,
            json=[
                _person(
                    **{
                        "customFields.DPR_Creation": big_document,
                        "customFields.GeoCheck": "OPTED",
                        "customFields.Doc_Status": "UPLOADED",
                        "customFields.Payment_Status": "PENDING",
                        "customFields.Payment_Mode": "PAY_LATER",
                    }
                )
            ],
        )
    )

    response = client.get("/api/v1/bd/customers", headers=AUTH)

    customer = response.json()["customers"][0]
    # Catalogue order, with our names and fees.
    assert customer["services"] == [
        {"code": "DPR_GENERATION", "name": "DPR Generation", "fee": 2000},
        {"code": "GEO_CHECK", "name": "Geo Check", "fee": 500},
    ]
    assert customer["doc_status"] == "UPLOADED"
    assert customer["payment_status"] == "PENDING"
    assert customer["payment_mode"] == "PAY_LATER"
    # A field's value can be a whole document; it must never reach the list.
    assert big_document not in response.text


def test_not_set_is_recognised_in_any_case_and_with_padding(client, vendor):
    vendor.post(BY_BROKER_ID).mock(
        return_value=httpx.Response(
            200,
            json=[
                _person(
                    **{
                        "customFields.GeoCheck": "na",
                        "customFields.DPR_Analysis": "",
                        "customFields.Doc_Status": " NA ",
                    }
                )
            ],
        )
    )
    customer = client.get("/api/v1/bd/customers", headers=AUTH).json()["customers"][0]
    assert customer["services"] == []
    assert customer["doc_status"] is None


def test_customers_are_listed_newest_first(client, vendor):
    vendor.post(BY_BROKER_ID).mock(
        return_value=httpx.Response(
            200,
            json=[
                _person(personId="old", createdAt="2026-10-01T00:00:00Z"),
                _person(personId="new", createdAt="2026-10-27T00:00:00Z"),
                _person(personId="mid", createdAt="2026-10-15T00:00:00Z"),
            ],
        )
    )
    body = client.get("/api/v1/bd/customers", headers=AUTH).json()
    assert [c["person_id"] for c in body["customers"]] == ["new", "mid", "old"]


def test_an_empty_array_is_an_empty_list(client, vendor):
    vendor.post(BY_BROKER_ID).mock(return_value=httpx.Response(200, json=[]))
    body = client.get("/api/v1/bd/customers", headers=AUTH).json()
    assert body == {"broker_id": BD_ID, "count": 0, "customers": []}


def test_the_saved_bd_id_is_accepted_when_it_matches_the_token(client, vendor):
    route = vendor.post(BY_BROKER_ID).mock(return_value=httpx.Response(200, json=[]))
    response = client.get(f"/api/v1/bd/customers?broker_id={BD_ID}", headers=AUTH)
    assert response.status_code == 200
    assert json.loads(route.calls.last.request.read())["brokerId"] == BD_ID


def test_the_match_ignores_case_because_keycloak_lowercases_usernames(client, vendor):
    route = vendor.post(BY_BROKER_ID).mock(return_value=httpx.Response(200, json=[]))
    auth = {"Authorization": f"Bearer {bd_jwt('brk001')}"}
    response = client.get("/api/v1/bd/customers?broker_id=BRK001", headers=auth)
    assert response.status_code == 200
    # The BD id as saved at login, not the lowercased claim.
    assert json.loads(route.calls.last.request.read())["brokerId"] == "BRK001"


def test_a_bd_cannot_list_another_bds_customers(client, vendor):
    response = client.get("/api/v1/bd/customers?broker_id=someone-else", headers=AUTH)
    assert response.status_code == 403
    assert not vendor.calls


def test_requires_a_bearer_token(client, vendor):
    assert client.get("/api/v1/bd/customers").status_code == 401
    assert not vendor.calls


def test_a_token_without_a_username_is_rejected(client, vendor):
    auth = {"Authorization": f"Bearer {make_jwt({'sub': 'x'})}"}
    assert client.get("/api/v1/bd/customers", headers=auth).status_code == 401
    assert not vendor.calls


def test_vendor_401_maps_to_401(client, vendor):
    vendor.post(BY_BROKER_ID).mock(
        return_value=httpx.Response(401, json=error_envelope(status=401))
    )
    assert client.get("/api/v1/bd/customers", headers=AUTH).status_code == 401


def test_the_list_is_a_retried_read(client, vendor):
    route = vendor.post(BY_BROKER_ID).mock(
        side_effect=[
            httpx.Response(500, json=error_envelope(status=500)),
            httpx.Response(200, json=[REAL_PERSON]),
        ]
    )
    response = client.get("/api/v1/bd/customers", headers=AUTH)
    assert response.status_code == 200
    assert route.call_count == 2


# --- one customer (getPersonDetailsByPersonId) ---------------------------

BY_PERSON_ID = "/walletInfoRetrieval/customerMgmtService/getPersonDetailsByPersonId"


def test_gets_one_customer_of_the_signed_in_bd(client, vendor):
    # The live by-id body (2026-10-27) is identical to a list element.
    route = vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(200, json=REAL_PERSON)
    )

    response = client.get("/api/v1/bd/customers/9000000013", headers=AUTH)

    assert response.status_code == 200
    assert json.loads(route.calls.last.request.read()) == {
        "personId": "9000000013",
        "programManagerId": "LAS",
    }
    body = response.json()
    assert body["person_id"] == "9000000013"
    assert body["first_name"] == "Kunal"
    assert body["address"]["street_address"] == "NO 06 yashod nagar"
    assert body["services"] == []
    assert "1234567890" not in response.text


def test_another_brokers_customer_is_not_found(client, vendor):
    vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(200, json=_person(**{"brokerId.id": "11111"}))
    )
    response = client.get("/api/v1/bd/customers/9000000013", headers=AUTH)
    assert response.status_code == 404
    assert "Kunal" not in response.text


def test_a_person_with_no_broker_is_not_found(client, vendor):
    person = {k: v for k, v in REAL_PERSON.items() if k != "brokerId.id"}
    vendor.post(BY_PERSON_ID).mock(return_value=httpx.Response(200, json=person))
    assert (
        client.get("/api/v1/bd/customers/9000000013", headers=AUTH).status_code == 404
    )


def test_unknown_and_foreign_customers_look_the_same(client, vendor):
    """Otherwise a BD could probe which person ids exist under other brokers."""
    vendor.post(BY_PERSON_ID).mock(
        side_effect=[
            httpx.Response(404, json=error_envelope(message="PERSON_NOT_FOUND")),
            httpx.Response(200, json=_person(**{"brokerId.id": "11111"})),
        ]
    )
    unknown = client.get("/api/v1/bd/customers/nobody", headers=AUTH)
    foreign = client.get("/api/v1/bd/customers/9000000013", headers=AUTH)
    assert unknown.status_code == foreign.status_code == 404
    assert unknown.json() == foreign.json()


def test_one_customer_checks_the_saved_bd_id_against_the_token(client, vendor):
    response = client.get(
        "/api/v1/bd/customers/9000000013?broker_id=someone-else", headers=AUTH
    )
    assert response.status_code == 403
    assert not vendor.calls


def test_one_customer_vendor_500_maps_to_502_after_retries(client, vendor):
    route = vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(500, json=error_envelope(status=500))
    )
    response = client.get("/api/v1/bd/customers/9000000013", headers=AUTH)
    assert response.status_code == 502
    assert route.call_count == 3
