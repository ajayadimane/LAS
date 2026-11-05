"""Details a service asks for when chosen: DPR Analysis -> bank and project.

Written with the single-value API (user, 2026-11-05), one PUT per field,
before anything is opted or charged.
"""

import json

import httpx
import pytest

from tests.conftest import ack, customer_jwt, error_envelope
from tests.test_bd import BY_PERSON_ID
from tests.test_enrolment import (
    AUTH,
    BASE,
    DPR_DETAILS,
    FIELD_VALUE,
    FIELDS,
    PID,
    _bodies,
    _person,
)

ME = {"Authorization": f"Bearer {customer_jwt(PID)}"}
PAY_NOW = {"services": ["DPR_ANALYSIS"], "mode": "pay_now", "details": DPR_DETAILS}


@pytest.fixture
def person(vendor):
    def set_fields(**values: str):
        return vendor.post(BY_PERSON_ID).mock(
            return_value=httpx.Response(200, json=_person(**values))
        )

    set_fields()
    return set_fields


@pytest.fixture
def fields(vendor):
    return vendor.put(FIELDS).mock(return_value=httpx.Response(200, json=ack()))


@pytest.fixture
def field_value(vendor):
    return vendor.put(FIELD_VALUE).mock(return_value=httpx.Response(200, json=ack()))


def _pay(client, payload=PAY_NOW):
    return client.post(f"{BASE}/payment", json=payload, headers=AUTH)


def test_the_catalogue_says_what_dpr_analysis_asks_for(client):
    services = {s["code"]: s for s in client.get("/api/v1/services").json()["services"]}

    assert services["DPR_ANALYSIS"]["details"] == [
        {"key": "bank_name", "label": "Bank name", "max_length": 100},
        {"key": "project_name", "label": "Project name", "max_length": 100},
    ]
    assert services["GEO_CHECK"]["details"] == []
    # The vendor field ids are ours to know, not the dashboard's.
    assert "BankName" not in json.dumps(services)


def test_each_detail_is_one_single_value_update_before_the_payment(
    client, vendor, person, field_value, fields
):
    order = []
    field_value.side_effect = lambda r: (
        order.append("detail") or httpx.Response(200, json=ack())
    )
    fields.side_effect = lambda r: (
        order.append("payment") or httpx.Response(200, json=ack())
    )

    response = _pay(client)

    assert response.status_code == 200, response.text
    assert _bodies(field_value) == [
        {
            "personId": PID,
            "programManagerId": "LAS",
            "customFieldId": "BankName",
            "customFieldValue": "SBI",
        },
        {
            "personId": PID,
            "programManagerId": "LAS",
            "customFieldId": "Project_Name",
            "customFieldValue": "Dairy unit",
        },
    ]
    assert order == ["detail", "detail", "payment"]


@pytest.mark.parametrize(
    "details, message",
    [
        ({}, "needs the bank name"),
        ({"DPR_ANALYSIS": {"bank_name": "SBI"}}, "needs the project name"),
        ({"DPR_ANALYSIS": {"bank_name": "  ", "project_name": "x"}}, "bank name"),
        ({"DPR_ANALYSIS": {"bank_name": "NA", "project_name": "x"}}, "bank name"),
        (
            {"DPR_ANALYSIS": {"bank_name": "x" * 101, "project_name": "x"}},
            "at most 100",
        ),
        (
            {"DPR_ANALYSIS": {**DPR_DETAILS["DPR_ANALYSIS"], "branch": "x"}},
            "does not take: branch",
        ),
        ({**DPR_DETAILS, "GEO_CHECK": {"bank_name": "x"}}, "not chosen: GEO_CHECK"),
    ],
)
def test_bad_details_are_refused_before_any_vendor_call(
    client, vendor, field_value, details, message
):
    response = _pay(client, {**PAY_NOW, "details": details})

    assert response.status_code == 422
    assert message in json.dumps(response.json())
    assert not field_value.called
    assert not vendor.calls.called


def test_values_are_trimmed(client, person, field_value, fields):
    details = {"DPR_ANALYSIS": {"bank_name": "  State  Bank ", "project_name": "P"}}

    _pay(client, {**PAY_NOW, "details": details})

    assert _bodies(field_value)[0]["customFieldValue"] == "State Bank"


def test_an_unchanged_detail_is_not_written_again(client, person, field_value, fields):
    person(BankName="SBI", Project_Name="Dairy unit")

    assert _pay(client).status_code == 200
    assert not field_value.called


def test_an_older_customer_without_the_fields_is_not_charged(
    client, person, field_value, fields
):
    """Verified live 2026-11-05: customers registered before BankName existed
    get CUSTOM_FIELD_ID_NOT_EXISTS. Nothing else may happen."""
    field_value.mock(
        return_value=httpx.Response(
            400, json=error_envelope(400, "CUSTOM_FIELD_ID_NOT_EXISTS")
        )
    )

    response = _pay(client)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CUSTOM_FIELD_ID_NOT_EXISTS"
    assert "Nothing was charged" in response.json()["error"]["message"]
    assert not fields.called


def test_a_detail_write_is_never_retried(client, person, field_value, fields):
    field_value.mock(return_value=httpx.Response(500, json=error_envelope(500)))

    assert _pay(client).status_code == 502
    assert field_value.call_count == 1
    assert not fields.called


def test_the_services_show_the_details(client, person):
    person(DPR_Analysis="Opted", BankName="SBI", Project_Name="NA")

    body = client.get(f"{BASE}/services", headers=AUTH).json()
    dpr = next(s for s in body["services"] if s["code"] == "DPR_ANALYSIS")

    assert dpr["details"] == [
        {"key": "bank_name", "label": "Bank name", "value": "SBI"},
        {"key": "project_name", "label": "Project name", "value": None},
    ]


def test_a_customer_gives_the_details_with_their_request(
    client, person, field_value, fields
):
    response = client.post(
        "/api/v1/customer/services/request",
        json={**PAY_NOW, "mode": "pay_later", "customer_consent": True},
        headers=ME,
    )

    assert response.status_code == 200, response.text
    assert [b["customFieldId"] for b in _bodies(field_value)] == [
        "BankName",
        "Project_Name",
    ]


def test_a_customer_request_without_details_is_refused(client, vendor, field_value):
    response = client.post(
        "/api/v1/customer/services/request",
        json={"services": ["DPR_ANALYSIS"], "mode": "pay_now"},
        headers=ME,
    )

    assert response.status_code == 422
    assert not vendor.calls.called


def test_new_customers_are_registered_with_the_detail_fields(settings):
    from app.services.service_fields import all_field_ids

    ids = all_field_ids(settings)
    assert {"BankName", "Project_Name"} <= set(ids)
    assert len(ids) == len(set(ids))


def test_fields_route_constant_is_the_bulk_one():
    assert FIELDS != FIELD_VALUE
