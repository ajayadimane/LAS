"""BD vs customer, from the token's groupId: each side's routes refuse the other."""

import httpx
import pytest

from tests.conftest import bd_jwt, customer_jwt, make_jwt
from tests.test_bd import BY_BROKER_ID, BY_PERSON_ID, REAL_PERSON

BD = {"Authorization": f"Bearer {bd_jwt()}"}
CUSTOMER = {"Authorization": f"Bearer {customer_jwt()}"}
NOBODY = {"Authorization": f"Bearer {make_jwt({'preferred_username': 'x'})}"}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/bd/customers"),
        ("get", "/api/v1/bd/customers/9000000013"),
        ("post", "/api/v1/bd/customers/9000000013/services/GEO_CHECK/decline"),
        # The general routes are not scoped to a broker: never for customers.
        ("get", "/api/v1/persons/9000000013"),
        ("get", "/api/v1/custom-fields"),
    ],
)
def test_a_customer_cannot_use_bd_routes(client, vendor, method, path):
    response = getattr(client, method)(path, headers=CUSTOMER)
    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "BD_ONLY"
    assert "signed in as a customer" in error["message"]
    assert not vendor.calls


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/customer/services"),
        ("post", "/api/v1/customer/services/request"),
    ],
)
def test_a_bd_cannot_use_customer_routes(client, vendor, method, path):
    kwargs = (
        {"json": {"services": ["GEO_CHECK"], "mode": "pay_now"}}
        if method == "post"
        else {}
    )
    response = getattr(client, method)(path, headers=BD, **kwargs)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CUSTOMER_ONLY"
    assert not vendor.calls


def test_a_token_in_no_dashboard_group_is_refused_everywhere(client, vendor):
    for path in ("/api/v1/bd/customers", "/api/v1/customer/services"):
        response = client.get(path, headers=NOBODY)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "NOT_A_DASHBOARD_USER"
    assert not vendor.calls


def test_each_side_still_reaches_its_own_routes(client, vendor):
    vendor.post(BY_BROKER_ID).mock(return_value=httpx.Response(200, json=[]))
    vendor.post(BY_PERSON_ID).mock(return_value=httpx.Response(200, json=REAL_PERSON))
    assert client.get("/api/v1/bd/customers", headers=BD).status_code == 200
    assert client.get("/api/v1/customer/services", headers=CUSTOMER).status_code == 200
