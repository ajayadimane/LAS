"""Custom field endpoints, with the vendor mocked by respx."""

import json

import httpx

from tests.conftest import ack, bd_jwt, error_envelope

CREATE_PATH = "/customermgmt/customField/programManager/"
LIST_PATH = "/walletInfoRetrieval/customerMgmtService/customFields"
UPDATE_VALUE_PATH = "/customermgmt/person/customField/value"
UPDATE_VALUES_PATH = "/customermgmt/person/customFields"

BD_TOKEN = bd_jwt()
AUTH = {"Authorization": f"Bearer {BD_TOKEN}"}

VALID_FIELD = {
    "custom_field_id": "pan",
    "custom_field_name": "panCard",
    "custom_field_description": "Add pancard",
    "custom_field_data_type": "Text",
    "is_searchable": True,
    "is_unique": True,
    "search_scope": "PROGRAM_MANAGER",
}


def _sent(route):
    return json.loads(route.calls.last.request.read().decode())


# --- define a custom field ----------------------------------------------


def test_create_custom_field_succeeds(client, vendor):
    vendor.post(CREATE_PATH).mock(return_value=httpx.Response(201, json=ack()))
    response = client.post("/api/v1/custom-fields", json=VALID_FIELD, headers=AUTH)
    assert response.status_code == 201
    assert response.json()["correlation_id"] == "7341821415336603648"


def test_create_custom_field_maps_our_names_to_the_vendors(client, vendor):
    route = vendor.post(CREATE_PATH).mock(return_value=httpx.Response(201, json=ack()))
    client.post("/api/v1/custom-fields", json=VALID_FIELD, headers=AUTH)

    assert _sent(route) == {
        "programManagerId": "LAS",
        "customFieldId": "pan",
        "customFieldDataType": "Text",
        "customFieldName": "panCard",
        "customFieldDescription": "Add pancard",
        "isSearchable": True,
        "isUnique": True,
        "searchScope": "PROGRAM_MANAGER",
    }


def test_create_custom_field_requires_a_bearer_token(client, vendor):
    assert client.post("/api/v1/custom-fields", json=VALID_FIELD).status_code == 401
    assert not vendor.calls


def test_create_rejects_an_unsupported_data_type(client, vendor):
    """The vendor allows only Text and List."""
    bad = dict(VALID_FIELD, custom_field_data_type="Integer")
    response = client.post("/api/v1/custom-fields", json=bad, headers=AUTH)
    assert response.status_code == 422
    assert not vendor.calls


def test_create_rejects_an_unsupported_search_scope(client, vendor):
    bad = dict(VALID_FIELD, search_scope="EVERYWHERE")
    response = client.post("/api/v1/custom-fields", json=bad, headers=AUTH)
    assert response.status_code == 422
    assert not vendor.calls


def test_create_duplicate_field_maps_to_409(client, vendor):
    vendor.post(CREATE_PATH).mock(
        return_value=httpx.Response(
            409, json=error_envelope(status=409, message="CUSTOM_FIELD_ALREADY_EXISTS")
        )
    )
    response = client.post("/api/v1/custom-fields", json=VALID_FIELD, headers=AUTH)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CUSTOM_FIELD_ALREADY_EXISTS"


# --- list ---------------------------------------------------------------


def test_list_custom_fields_maps_the_array(client, vendor):
    vendor.post(LIST_PATH).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "customFieldId": "pan",
                    "customFieldName": "panCard",
                    "customFieldDataType": "Text",
                    "isSearchable": True,
                    "isUnique": True,
                    "searchScope": "PROGRAM_MANAGER",
                }
            ],
        )
    )
    response = client.get("/api/v1/custom-fields", headers=AUTH)
    body = response.json()
    assert body["program_manager_id"] == "LAS"
    assert body["custom_fields"][0]["custom_field_id"] == "pan"
    assert body["custom_fields"][0]["is_unique"] is True


# Two entries of the real list for program manager LAS (user, 2026-10-27).
REAL_DEFINITIONS = [
    {
        "dataType": "TEXT",
        "searchScope": "GLOBAL",
        "customFieldId.id": "DPR_Creation",
        "name": "DPR_Creation",
        "isUnique": "false",
        "description": "DPR Creation",
        "programManagerId.id": "LAS",
        "isSearchable": "true",
        "mappedSearchKey": "SK8",
    },
    {
        "dataType": "TEXT",
        "searchScope": "PROGRAM_MANAGER",
        "customFieldId.id": "Payment_Status",
        "name": "Payment_Status",
        "isUnique": "false",
        "description": "to check paymet status of the customer",
        "programManagerId.id": "LAS",
        "isSearchable": "true",
        "mappedSearchKey": "SK5",
    },
]


def test_list_custom_fields_reads_the_real_flattened_shape(client, vendor):
    vendor.post(LIST_PATH).mock(return_value=httpx.Response(200, json=REAL_DEFINITIONS))
    body = client.get("/api/v1/custom-fields", headers=AUTH).json()
    assert body["custom_fields"][0] == {
        "custom_field_id": "DPR_Creation",
        "custom_field_name": "DPR_Creation",
        "custom_field_description": "DPR Creation",
        "custom_field_data_type": "TEXT",
        "is_searchable": True,
        "is_unique": False,
        "search_scope": "GLOBAL",
        "mapped_search_key": "SK8",
    }
    assert body["custom_fields"][1]["custom_field_id"] == "Payment_Status"


def test_list_custom_fields_drops_undocumented_vendor_keys(client, vendor):
    """The response shape is undocumented, so unknown keys must not pass through."""
    vendor.post(LIST_PATH).mock(
        return_value=httpx.Response(
            200, json=[{"customFieldId": "pan", "somethingNew": "surprise"}]
        )
    )
    text = client.get("/api/v1/custom-fields", headers=AUTH).text
    assert "somethingNew" not in text
    assert "surprise" not in text


def test_list_custom_fields_handles_an_empty_array(client, vendor):
    vendor.post(LIST_PATH).mock(return_value=httpx.Response(200, json=[]))
    assert (
        client.get("/api/v1/custom-fields", headers=AUTH).json()["custom_fields"] == []
    )


def test_list_custom_fields_tolerates_a_non_list_body(client, vendor):
    """Documented only as "a JSON array"; do not fall over if it is not one."""
    vendor.post(LIST_PATH).mock(return_value=httpx.Response(200, json={"oops": True}))
    response = client.get("/api/v1/custom-fields", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["custom_fields"] == []


def test_list_custom_fields_is_retried(client, vendor):
    route = vendor.post(LIST_PATH).mock(
        side_effect=[
            httpx.Response(500, json=error_envelope(status=500)),
            httpx.Response(200, json=[]),
        ]
    )
    assert client.get("/api/v1/custom-fields", headers=AUTH).status_code == 200
    assert route.call_count == 2


# --- update one value ---------------------------------------------------


def test_update_single_value_uses_put_and_maps_the_payload(client, vendor):
    route = vendor.put(UPDATE_VALUE_PATH).mock(
        return_value=httpx.Response(200, json=ack())
    )
    response = client.put(
        "/api/v1/persons/88655489/custom-fields/pan",
        json={"custom_field_value": "ABCDE1234F"},
        headers=AUTH,
    )
    assert response.status_code == 200
    assert _sent(route) == {
        "personId": "88655489",
        "programManagerId": "LAS",
        "customFieldId": "pan",
        "customFieldValue": "ABCDE1234F",
    }


def test_update_single_value_requires_a_bearer_token(client, vendor):
    response = client.put(
        "/api/v1/persons/88655489/custom-fields/pan",
        json={"custom_field_value": "x"},
    )
    assert response.status_code == 401
    assert not vendor.calls


def test_update_single_value_is_never_retried(client, vendor):
    route = vendor.put(UPDATE_VALUE_PATH).mock(
        return_value=httpx.Response(500, json=error_envelope(status=500))
    )
    client.put(
        "/api/v1/persons/88655489/custom-fields/pan",
        json={"custom_field_value": "x"},
        headers=AUTH,
    )
    assert route.call_count == 1


# --- update many values -------------------------------------------------


def test_update_many_values_maps_the_payload(client, vendor):
    route = vendor.put(UPDATE_VALUES_PATH).mock(
        return_value=httpx.Response(200, json=ack())
    )
    response = client.put(
        "/api/v1/persons/88655489/custom-fields",
        json={"custom_fields": {"pan": "ABCDE1234F", "aadhar": "123456789012"}},
        headers=AUTH,
    )
    assert response.status_code == 200
    assert _sent(route) == {
        "personId": "88655489",
        "programManagerId": "LAS",
        "customFields": {"pan": "ABCDE1234F", "aadhar": "123456789012"},
    }


def test_update_many_rejects_an_empty_map(client, vendor):
    response = client.put(
        "/api/v1/persons/88655489/custom-fields",
        json={"custom_fields": {}},
        headers=AUTH,
    )
    assert response.status_code == 422
    assert not vendor.calls


def test_update_many_person_not_found_maps_to_404(client, vendor):
    vendor.put(UPDATE_VALUES_PATH).mock(
        return_value=httpx.Response(
            404, json=error_envelope(message="PERSON_NOT_FOUND")
        )
    )
    response = client.put(
        "/api/v1/persons/ghost/custom-fields",
        json={"custom_fields": {"pan": "x"}},
        headers=AUTH,
    )
    assert response.status_code == 404


# --- routing ------------------------------------------------------------


def test_person_lookup_and_custom_field_routes_do_not_shadow_each_other(client, vendor):
    """GET /persons/{id} and PUT /persons/{id}/custom-fields must both resolve."""
    vendor.post(
        "/walletInfoRetrieval/customerMgmtService/getPersonDetailsByPersonId"
    ).mock(return_value=httpx.Response(200, json={"personId": "88655489"}))
    vendor.put(UPDATE_VALUES_PATH).mock(return_value=httpx.Response(200, json=ack()))

    assert client.get("/api/v1/persons/88655489", headers=AUTH).status_code == 200
    assert (
        client.put(
            "/api/v1/persons/88655489/custom-fields",
            json={"custom_fields": {"pan": "x"}},
            headers=AUTH,
        ).status_code
        == 200
    )
