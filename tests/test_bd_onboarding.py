"""BD adds a customer: person registration, wallet creation, login creation."""

import json
import logging

import httpx
import pytest

from tests.conftest import ack, error_envelope, make_jwt
from tests.test_bd import AUTH, BD_ID, BY_PERSON_ID, REAL_PERSON

REGISTER = "/customermgmt/person/"
WALLET = "/regularWallet/"
ADD_USER = "/IdentityAccessMgmt/user/"
SUPERADMIN = "/IdentityAccessMgmt/user/superadmin"

ADMIN_TOKEN = make_jwt(
    {"preferred_username": "service-account-ewallet-cli-client", "exp": 4102444800}
)


@pytest.fixture(autouse=True)
def admin_route(vendor):
    """The middleware's own super-admin sign-in. Shape from the user, 2026-10-27."""
    return vendor.post(SUPERADMIN).mock(
        return_value=httpx.Response(200, json=ack(accessToken=ADMIN_TOKEN))
    )


@pytest.fixture(autouse=True)
def login_route(vendor):
    """Login creation succeeds unless a test says otherwise. respx replaces
    this route when a test registers the same path again."""
    return vendor.post(ADD_USER).mock(return_value=httpx.Response(201, json=ack()))


FORM = {
    "first_name": "Kunal",
    "last_name": "B",
    "mobile_number": "9000000013",
    "email_address": "email@nomail.com",
    "street_address": "NO 06 yashod nagar",
    "city": "banglore",
    "state_province": "karnataka",
    "postal_code": "560091",
}

# The live wallet ack, 2026-10-27.
WALLET_ACK = {
    "transactionId": "7520787488333262848",
    "correlationId": "b56886e5-41af-490e-ace2-325d43a8a41a",
    "timeStamp": "2026-10-27T10:04:26.883865654Z",
}
REGISTER_ACK = {
    "transactionId": "7520787488333260000",
    "correlationId": "reg-corr",
    "timeStamp": "2026-10-27T10:04:26.000Z",
}


def _body(route) -> dict:
    return json.loads(route.calls.last.request.read())


def test_registers_then_opens_the_wallet(client, vendor):
    register = vendor.post(REGISTER).mock(
        return_value=httpx.Response(201, json=REGISTER_ACK)
    )
    wallet = vendor.post(WALLET).mock(return_value=httpx.Response(201, json=WALLET_ACK))

    response = client.post("/api/v1/bd/customers", json=FORM, headers=AUTH)

    assert response.status_code == 201
    # Exactly the body the user specified, 2026-10-27.
    assert _body(register) == {
        "personId": "9000000013",
        "brokerId": BD_ID,
        "emailAddress": "email@nomail.com",
        "addressStreetAddress": "NO 06 yashod nagar",
        "addressCity": "banglore",
        "addressStateProvince": "karnataka",
        "addressPostalCode": "560091",
        "addressCountryCode": "IN",
        "mobileNumber": "9000000013",
        "mobileCountryCode": "91",
        "firstName": "Kunal",
        "lastName": "B",
        "settlementAccountNo": "1234567890",
        "settlementAccountIfscCode": "ICIC0001010",
        "programManagerId": "LAS",
        "groupId": "group1",
        # Every LAS field, "NA": per service the lifecycle field plus
        # _Payment/_PayMode/_TxnId/_Doc, then the three summaries.
        "customFields": {
            f: "NA"
            for service in (
                "DPR_Creation",
                "DPR_Analysis",
                "GeoCheck",
                "Doc_Verification",
            )
            for f in (
                service,
                f"{service}_Payment",
                f"{service}_PayMode",
                f"{service}_TxnId",
                f"{service}_Doc",
            )
        }
        | {"Doc_Status": "NA", "Payment_Status": "NA", "Payment_Mode": "NA"},
    }
    # The short spelling, on this path only.
    assert _body(wallet) == {
        "programMngrId": "LAS",
        "personId": "9000000013",
        "groupId": "group1",
        "tagId": "tag1",
    }
    assert register.calls.last.request.headers["authorization"] == AUTH["Authorization"]

    body = response.json()
    assert body["person_id"] == "9000000013"
    assert body["wallet_opened"] is True
    assert body["wallet_error"] is None
    assert body["registration"]["transaction_id"] == "7520787488333260000"
    assert body["wallet"]["transaction_id"] == "7520787488333262848"
    assert body["wallet"]["correlation_id"] == "b56886e5-41af-490e-ace2-325d43a8a41a"


def test_registration_then_wallet_then_login(client, vendor):
    order = []
    for path, name in ((REGISTER, "register"), (WALLET, "wallet"), (ADD_USER, "login")):
        vendor.post(path).mock(
            side_effect=lambda r, n=name: (
                order.append(n) or httpx.Response(201, json={})
            )
        )
    client.post("/api/v1/bd/customers", json=FORM, headers=AUTH)
    assert order == ["register", "wallet", "login"]


# --- login creation ------------------------------------------------------


def test_creates_the_customers_login_with_a_generated_password(
    client, vendor, login_route
):
    vendor.post(REGISTER).mock(return_value=httpx.Response(201, json=REGISTER_ACK))
    vendor.post(WALLET).mock(return_value=httpx.Response(201, json=WALLET_ACK))

    response = client.post("/api/v1/bd/customers", json=FORM, headers=AUTH)

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    sent = _body(login_route)
    password = sent.pop("password")
    # The user's spec, 2026-10-27: userName and mobileNumber are the mobile.
    assert sent == {
        "userName": "9000000013",
        "email": "email@nomail.com",
        "firstName": "Kunal",
        "lastName": "B",
        "programManagerId": "LAS",
        "groupName": "LAS.enduser",
        "mobileCountryCode": "91",
        "mobileNumber": "9000000013",
        "twoFactorAuthCodeType": "OTP",
        "twoFactorAuthCodeDeliveryChannel": "SMS",
    }
    body = response.json()
    assert body["login_created"] is True
    assert body["login_user_id"] == "9000000013"
    assert body["login"]["transaction_id"] == "7345430166900109312"
    # The BD gets exactly the password the vendor was given.
    assert body["temporary_password"] == password
    assert len(password) == 12


def test_each_customer_gets_a_different_password(client, vendor):
    vendor.post(REGISTER).mock(return_value=httpx.Response(201, json={}))
    vendor.post(WALLET).mock(return_value=httpx.Response(201, json={}))
    first = client.post("/api/v1/bd/customers", json=FORM, headers=AUTH).json()
    second = client.post("/api/v1/bd/customers", json=FORM, headers=AUTH).json()
    assert first["temporary_password"] != second["temporary_password"]


def test_the_password_never_reaches_the_logs(client, vendor, caplog):
    vendor.post(REGISTER).mock(return_value=httpx.Response(201, json={}))
    vendor.post(WALLET).mock(return_value=httpx.Response(201, json={}))
    with caplog.at_level(logging.DEBUG):
        body = client.post("/api/v1/bd/customers", json=FORM, headers=AUTH).json()
    assert body["temporary_password"] not in caplog.text


def test_a_missing_last_name_is_sent_as_na(client, vendor, login_route):
    vendor.post(REGISTER).mock(return_value=httpx.Response(201, json={}))
    vendor.post(WALLET).mock(return_value=httpx.Response(201, json={}))
    form = {k: v for k, v in FORM.items() if k != "last_name"}
    client.post("/api/v1/bd/customers", json=form, headers=AUTH)
    assert _body(login_route)["lastName"] == "NA"


def test_a_failed_login_still_reports_the_customer_as_registered(client, vendor):
    vendor.post(REGISTER).mock(return_value=httpx.Response(201, json={}))
    vendor.post(WALLET).mock(return_value=httpx.Response(201, json={}))
    login = vendor.post(ADD_USER).mock(
        return_value=httpx.Response(500, json=error_envelope(status=500))
    )

    response = client.post("/api/v1/bd/customers", json=FORM, headers=AUTH)

    assert response.status_code == 201
    body = response.json()
    assert body["wallet_opened"] is True
    assert body["login_created"] is False
    assert body["temporary_password"] is None
    assert body["login_error"]["status"] == 502
    assert login.call_count == 1  # a write: never retried


def test_the_login_is_attempted_even_if_the_wallet_failed(client, vendor, login_route):
    vendor.post(REGISTER).mock(return_value=httpx.Response(201, json={}))
    vendor.post(WALLET).mock(return_value=httpx.Response(500, json=error_envelope(500)))
    body = client.post("/api/v1/bd/customers", json=FORM, headers=AUTH).json()
    assert body["wallet_opened"] is False
    assert body["login_created"] is True
    assert login_route.called


def test_an_existing_login_is_not_reported_as_created(client, vendor):
    vendor.post(REGISTER).mock(return_value=httpx.Response(201, json={}))
    vendor.post(WALLET).mock(return_value=httpx.Response(201, json={}))
    vendor.post(ADD_USER).mock(
        return_value=httpx.Response(
            409, json=error_envelope(409, "USER_ALREADY_EXISTS")
        )
    )
    body = client.post("/api/v1/bd/customers", json=FORM, headers=AUTH).json()
    assert body["login_created"] is False
    assert body["temporary_password"] is None
    assert body["login_error"]["code"] == "USER_ALREADY_EXISTS"


def test_a_raw_upstream_error_is_not_passed_to_the_dashboard(client, vendor):
    # Captured live, 2026-10-27: a BD token may not create IAM users.
    raw = (
        "403 Forbidden from GET http://keycloak:8080/admin/realms/ewallet/users/"
        "?username=9000000901"
    )
    envelope = {
        **error_envelope(500, raw),
        "transactionId": "null",
        "correlationId": "null",
    }
    vendor.post(REGISTER).mock(return_value=httpx.Response(201, json={}))
    vendor.post(WALLET).mock(return_value=httpx.Response(201, json={}))
    vendor.post(ADD_USER).mock(return_value=httpx.Response(500, json=envelope))

    response = client.post("/api/v1/bd/customers", json=FORM, headers=AUTH)

    assert "keycloak" not in response.text
    error = response.json()["login_error"]
    assert error["code"] is None
    assert error["transaction_id"] is None
    assert error["correlation_id"] is None


# --- the admin token -----------------------------------------------------


def _onboard(client, vendor):
    vendor.post(REGISTER).mock(return_value=httpx.Response(201, json={}))
    vendor.post(WALLET).mock(return_value=httpx.Response(201, json={}))
    return client.post("/api/v1/bd/customers", json=FORM, headers=AUTH)


def test_the_login_is_created_with_the_admin_token_not_the_bds(
    client, vendor, admin_route, login_route
):
    _onboard(client, vendor)

    assert json.loads(admin_route.calls.last.request.read()) == {
        "userId": "test-admin",
        "password": "test-admin-password",
    }
    # The gateway wants a signed-in user's bearer even on this sign-in.
    admin_headers = admin_route.calls.last.request.headers
    assert admin_headers["authorization"] == AUTH["Authorization"]
    assert "x-original-forwarded-for" in admin_headers
    assert login_route.calls.last.request.headers["authorization"] == (
        f"Bearer {ADMIN_TOKEN}"
    )
    # Every other step keeps the BD's own token.
    for call in vendor.calls:
        path = call.request.url.path
        if path not in (ADD_USER, SUPERADMIN):
            assert call.request.headers["authorization"] == AUTH["Authorization"]


def test_the_admin_token_is_reused_until_it_expires(client, vendor, admin_route):
    _onboard(client, vendor)
    _onboard(client, vendor)
    assert admin_route.call_count == 1


def test_a_rejected_admin_token_is_renewed_once(client, vendor, admin_route):
    login = vendor.post(ADD_USER).mock(
        side_effect=[httpx.Response(401), httpx.Response(201, json=ack())]
    )
    body = _onboard(client, vendor).json()
    assert body["login_created"] is True
    assert admin_route.call_count == 2
    assert login.call_count == 2


def test_an_admin_token_rejected_twice_never_reads_as_the_bds_401(client, vendor):
    vendor.post(BY_PERSON_ID).mock(return_value=httpx.Response(200, json=REAL_PERSON))
    vendor.post(ADD_USER).mock(return_value=httpx.Response(401))

    response = client.post("/api/v1/bd/customers/9000000013/login", headers=AUTH)

    # A 401 would make the dashboard sign the BD out.
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "ADMIN_TOKEN_REJECTED"


def test_a_failed_admin_sign_in_is_reported_on_the_login_step(
    client, vendor, login_route
):
    vendor.post(SUPERADMIN).mock(
        return_value=httpx.Response(
            400, json=error_envelope(400, "INVALID_CREDENTIALS_TO_AUTHENTICATE_USER")
        )
    )
    response = _onboard(client, vendor)

    assert response.status_code == 201
    error = response.json()["login_error"]
    # Not "The user ID or password is incorrect": the BD typed no password.
    assert (error["status"], error["code"]) == (502, "ADMIN_LOGIN_FAILED")
    assert "incorrect" not in error["message"]
    assert not login_route.called


def test_without_admin_settings_no_login_is_attempted(settings, vendor, login_route):
    from fastapi.testclient import TestClient

    from app.main import create_app

    settings.vendor_admin_user_id = None
    with TestClient(create_app(settings)) as c:
        body = _onboard(c, vendor).json()
    assert body["login_error"]["code"] == "ADMIN_NOT_CONFIGURED"
    assert not login_route.called


# --- retrying just the login ---------------------------------------------


def test_create_login_retry_uses_the_person_record(client, vendor, login_route):
    vendor.post(BY_PERSON_ID).mock(return_value=httpx.Response(200, json=REAL_PERSON))

    response = client.post("/api/v1/bd/customers/9000000013/login", headers=AUTH)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    sent = _body(login_route)
    body = response.json()
    assert body["already_exists"] is False
    assert body["temporary_password"] == sent["password"]
    assert (sent["userName"], sent["firstName"], sent["email"]) == (
        "9000000013",
        "Kunal",
        "email@nomail.com",
    )


def test_create_login_retry_treats_409_as_existing(client, vendor):
    vendor.post(BY_PERSON_ID).mock(return_value=httpx.Response(200, json=REAL_PERSON))
    vendor.post(ADD_USER).mock(
        return_value=httpx.Response(409, json=error_envelope(status=409))
    )
    response = client.post("/api/v1/bd/customers/9000000013/login", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "person_id": "9000000013",
        "login_user_id": "9000000013",
        "already_exists": True,
        "login": None,
        "temporary_password": None,
        "credentials_emailed": False,
        "credentials_email_error": None,
    }


def test_create_login_retry_refuses_another_brokers_customer(
    client, vendor, login_route
):
    vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(200, json={**REAL_PERSON, "brokerId.id": "11111"})
    )
    response = client.post("/api/v1/bd/customers/9000000013/login", headers=AUTH)
    assert response.status_code == 404
    assert not login_route.called


def test_a_blank_last_name_is_left_out(client, vendor):
    register = vendor.post(REGISTER).mock(return_value=httpx.Response(201, json={}))
    vendor.post(WALLET).mock(return_value=httpx.Response(201, json={}))
    client.post("/api/v1/bd/customers", json={**FORM, "last_name": "  "}, headers=AUTH)
    assert "lastName" not in _body(register)


def test_a_failed_registration_never_opens_a_wallet(client, vendor):
    vendor.post(REGISTER).mock(
        return_value=httpx.Response(
            409, json=error_envelope(status=409, message="PERSON_ALREADY_EXISTS")
        )
    )
    wallet = vendor.post(WALLET).mock(return_value=httpx.Response(201, json={}))

    response = client.post("/api/v1/bd/customers", json=FORM, headers=AUTH)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PERSON_ALREADY_EXISTS"
    assert not wallet.called


def test_a_failed_wallet_still_reports_the_customer_as_registered(client, vendor):
    vendor.post(REGISTER).mock(return_value=httpx.Response(201, json=REGISTER_ACK))
    wallet = vendor.post(WALLET).mock(
        return_value=httpx.Response(500, json=error_envelope(status=500))
    )

    response = client.post("/api/v1/bd/customers", json=FORM, headers=AUTH)

    assert response.status_code == 201
    body = response.json()
    assert body["wallet_opened"] is False
    assert body["wallet"] is None
    error = body["wallet_error"]
    assert error["status"] == 502
    assert error["transaction_id"] == "7510946730856935424"
    assert "internal stack trace" not in response.text
    # Writes are never retried: one attempt only.
    assert wallet.call_count == 1


def test_registration_is_never_retried(client, vendor):
    register = vendor.post(REGISTER).mock(
        return_value=httpx.Response(500, json=error_envelope(status=500))
    )
    response = client.post("/api/v1/bd/customers", json=FORM, headers=AUTH)
    assert response.status_code == 502
    assert register.call_count == 1


def test_a_wallet_that_already_exists_counts_as_opened(client, vendor):
    vendor.post(REGISTER).mock(return_value=httpx.Response(201, json={}))
    vendor.post(WALLET).mock(
        return_value=httpx.Response(409, json=error_envelope(status=409))
    )
    body = client.post("/api/v1/bd/customers", json=FORM, headers=AUTH).json()
    assert body["wallet_opened"] is True


def test_a_bd_cannot_register_under_another_broker(client, vendor):
    response = client.post(
        "/api/v1/bd/customers?broker_id=someone-else", json=FORM, headers=AUTH
    )
    assert response.status_code == 403
    assert not vendor.calls


def test_requires_a_bearer_token(client, vendor):
    assert client.post("/api/v1/bd/customers", json=FORM).status_code == 401
    assert not vendor.calls


def test_invalid_input_is_rejected_before_any_vendor_call(client, vendor):
    for bad in (
        {**FORM, "mobile_number": "90000"},
        {**FORM, "mobile_number": "90000 00013"},
        {**FORM, "email_address": "not-an-email"},
        {**FORM, "postal_code": "123"},
        {**FORM, "state_province": "k"},
        {**FORM, "first_name": ""},
        {**FORM, "country_code": "IND"},
        {k: v for k, v in FORM.items() if k != "city"},
    ):
        response = client.post("/api/v1/bd/customers", json=bad, headers=AUTH)
        assert response.status_code == 422, bad
    assert not vendor.calls


# --- retrying just the wallet --------------------------------------------


def test_open_wallet_retry_checks_ownership_then_opens(client, vendor):
    vendor.post(BY_PERSON_ID).mock(return_value=httpx.Response(200, json=REAL_PERSON))
    wallet = vendor.post(WALLET).mock(return_value=httpx.Response(201, json=WALLET_ACK))

    response = client.post("/api/v1/bd/customers/9000000013/wallet", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["already_open"] is False
    assert _body(wallet)["personId"] == "9000000013"


def test_open_wallet_retry_treats_409_as_already_open(client, vendor):
    vendor.post(BY_PERSON_ID).mock(return_value=httpx.Response(200, json=REAL_PERSON))
    vendor.post(WALLET).mock(
        return_value=httpx.Response(409, json=error_envelope(status=409))
    )
    response = client.post("/api/v1/bd/customers/9000000013/wallet", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "person_id": "9000000013",
        "already_open": True,
        "wallet": None,
    }


def test_open_wallet_retry_refuses_another_brokers_customer(client, vendor):
    vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(200, json={**REAL_PERSON, "brokerId.id": "11111"})
    )
    wallet = vendor.post(WALLET).mock(return_value=httpx.Response(201, json={}))
    response = client.post("/api/v1/bd/customers/9000000013/wallet", headers=AUTH)
    assert response.status_code == 404
    assert not wallet.called
