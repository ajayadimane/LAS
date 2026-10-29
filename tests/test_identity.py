"""Endpoint tests for identity, with the vendor mocked by respx."""

import json
import logging
import time

import httpx
import pytest

from tests.conftest import (
    ADD_USER_PATH,
    FORGOT_PATH,
    LOGIN_PATH,
    ack,
    bd_jwt,
    error_envelope,
    make_jwt,
)

VALID_USER = {
    "user_name": "pavan",
    "email": "pavan@example.com",
    "first_name": "pavan",
    "last_name": "kalyan",
    "group_name": "ibkart.admin",
    "mobile_country_code": "91",
    "mobile_number": "8374543039",
    "password": "s3cret-pw",
}

BD_TOKEN = bd_jwt()
AUTH = {"Authorization": f"Bearer {BD_TOKEN}"}


def _sent_body(route):
    return json.loads(route.calls.last.request.read().decode())


# --- login --------------------------------------------------------------


def test_login_returns_our_schema_not_the_vendors(client, vendor):
    token = make_jwt({"preferred_username": "las", "groupId": ["LAS.admin"]})
    vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(200, json=ack(accessToken=token))
    )

    response = client.post(
        "/api/v1/identity/login", json={"user_id": "las", "password": "pw"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] == token
    assert body["token_type"] == "bearer"
    assert body["role"] == "bd"
    assert body["transaction_id"] == "7345430166900109312"
    assert body["correlation_id"] == "7341821415336603648"
    # The dashboard keeps this as the BD id or customer id.
    assert body["user_id"] == "las"
    # No exp claim to report.
    assert body["expires_in"] is None
    assert body["expires_at"] is None
    # No raw vendor field names leak through.
    assert "accessToken" not in body
    assert "timeStamp" not in body


def test_login_reports_expiry_from_the_tokens_exp_claim(client, vendor):
    now = int(time.time())
    # Same shape as a real sandbox token: 10 hours between iat and exp.
    token = make_jwt(
        {
            "iat": now,
            "exp": now + 36000,
            "preferred_username": "9000000012",
            "groupId": ["LAS.enduser"],
        }
    )
    vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(200, json=ack(accessToken=token))
    )

    response = client.post(
        "/api/v1/identity/login", json={"user_id": "9000000012", "password": "pw"}
    )

    body = response.json()
    assert body["access_token"] == token
    assert body["user_id"] == "9000000012"
    assert body["role"] == "customer"
    assert body["expires_at"] == now + 36000
    assert 35990 <= body["expires_in"] <= 36000


def test_login_reports_zero_seconds_left_for_an_already_expired_token(client, vendor):
    token = make_jwt({"exp": int(time.time()) - 60, "groupId": ["LAS.admin"]})
    vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(200, json=ack(accessToken=token))
    )
    response = client.post(
        "/api/v1/identity/login", json={"user_id": "las", "password": "pw"}
    )
    assert response.json()["expires_in"] == 0


@pytest.mark.parametrize(
    "claims",
    [
        {},
        {"groupId": []},
        {"groupId": ["Merchant"]},
        {"groupId": ["LAS.admin", "LAS.enduser"]},  # both: refuse, never guess
    ],
)
def test_an_account_that_is_neither_bd_nor_customer_is_refused(client, vendor, claims):
    vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(200, json=ack(accessToken=make_jwt(claims)))
    )
    response = client.post(
        "/api/v1/identity/login", json={"user_id": "x", "password": "pw"}
    )
    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "NOT_A_DASHBOARD_USER"
    assert "BD or a customer" in error["message"]
    assert "access_token" not in response.text


def test_the_group_claim_is_matched_without_case(client, vendor):
    vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(
            200, json=ack(accessToken=make_jwt({"groupId": "las.ENDUSER"}))
        )
    )
    body = client.post(
        "/api/v1/identity/login", json={"user_id": "x", "password": "pw"}
    ).json()
    assert body["role"] == "customer"


def test_login_bad_credentials_returns_400_with_a_friendly_message(client, vendor):
    # Captured from the live sandbox on 2026-10-27.
    vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(
            400,
            json={
                "timestamp": "27-10-2026 05:41:08",
                "transactionId": "7520721218851790848",
                "correlationId": "f6f8e490-866c-4552-b0c2-27a6d99c2051",
                "definedErrorCode": "INVALID_CREDENTIALS_TO_AUTHENTICATE_USER",
                "status": 400,
                "errorCode": "INVALID_CREDENTIALS_TO_AUTHENTICATE_USER",
                "errorMessage": "INVALID_CREDENTIALS_TO_AUTHENTICATE_USER",
                "debugMessage": "INVALID_CREDENTIALS_TO_AUTHENTICATE_USER",
            },
        )
    )

    response = client.post(
        "/api/v1/identity/login", json={"user_id": "9000000012", "password": "bad"}
    )

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "INVALID_CREDENTIALS_TO_AUTHENTICATE_USER"
    assert error["message"] != error["code"]
    assert error["transaction_id"] == "7520721218851790848"


def test_login_sends_the_short_program_manager_spelling(client, vendor):
    route = vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(200, json=ack(accessToken="t"))
    )

    client.post(
        "/api/v1/identity/login",
        json={"user_id": "las", "password": "pw", "program_manager_id": "ibkart"},
    )

    body = _sent_body(route)
    assert body == {"userId": "las", "programMngrId": "ibkart", "password": "pw"}


def test_login_falls_back_to_the_configured_program_manager(client, vendor):
    route = vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(200, json=ack(accessToken="t"))
    )
    client.post("/api/v1/identity/login", json={"user_id": "las", "password": "pw"})
    assert _sent_body(route)["programMngrId"] == "LAS"


def test_login_sends_no_authorization_header(client, vendor):
    route = vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(200, json=ack(accessToken="t"))
    )
    client.post("/api/v1/identity/login", json={"user_id": "las", "password": "pw"})
    assert "authorization" not in route.calls.last.request.headers


def test_login_forwards_the_end_user_ip_and_geolocation(client, vendor):
    route = vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(200, json=ack(accessToken="t"))
    )

    client.post(
        "/api/v1/identity/login",
        json={"user_id": "las", "password": "pw"},
        headers={
            "X-Forwarded-For": "198.51.100.9, 10.0.0.1",
            "X-Geolocation-Latitude": "19.0760",
            "X-Geolocation-Longitude": "72.8777",
        },
    )

    sent = route.calls.last.request.headers
    assert sent["x-original-forwarded-for"] == "198.51.100.9"
    assert sent["x-geolocation-latitude"] == "19.0760"
    assert sent["x-geolocation-longitude"] == "72.8777"


def test_login_uses_default_geolocation_when_dashboard_sends_none(client, vendor):
    route = vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(200, json=ack(accessToken="t"))
    )
    client.post("/api/v1/identity/login", json={"user_id": "las", "password": "pw"})
    sent = route.calls.last.request.headers
    assert sent["x-geolocation-latitude"] == "12.9716"
    assert sent["x-geolocation-longitude"] == "77.5946"


def test_login_failure_returns_the_mapped_status_and_hides_debug_message(
    client, vendor
):
    vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(404, json=error_envelope())
    )

    response = client.post(
        "/api/v1/identity/login", json={"user_id": "nope", "password": "pw"}
    )

    assert response.status_code == 404
    error = response.json()["error"]
    # The vendor's raw code is passed through for the dashboard to branch on...
    assert error["code"] == "PM_NOT_FOUND"
    # ...but the message is ours, not the vendor's machine code.
    assert error["message"] == (
        "That program manager could not be found. Check the value and try again."
    )
    assert "PM_NOT_FOUND" != error["message"]
    assert error["transaction_id"] == "7510946730856935424"
    assert error["correlation_id"] == "corr-123"
    # The vendor's debugMessage must never reach the dashboard.
    assert "internal stack trace" not in response.text
    assert "debug" not in response.text.lower()


def test_login_200_without_a_token_is_a_gateway_error(client, vendor):
    vendor.post(LOGIN_PATH).mock(return_value=httpx.Response(200, json=ack()))
    response = client.post(
        "/api/v1/identity/login", json={"user_id": "las", "password": "pw"}
    )
    assert response.status_code == 502
    # The internal diagnostic is logged, not shown.
    assert "access token" not in response.text.lower()
    assert response.json()["error"]["message"] == (
        "The service is temporarily unavailable. Please try again shortly."
    )


def test_login_requires_a_password(client, vendor):
    response = client.post("/api/v1/identity/login", json={"user_id": "las"})
    assert response.status_code == 422
    assert not vendor.calls


# --- create user --------------------------------------------------------


def test_create_user_requires_a_bearer_token(client, vendor):
    response = client.post("/api/v1/identity/users", json=VALID_USER)
    assert response.status_code == 401
    # Rejected before we spend a vendor round trip.
    assert not vendor.calls


def test_create_user_rejects_a_malformed_authorization_header(client, vendor):
    response = client.post(
        "/api/v1/identity/users", json=VALID_USER, headers={"Authorization": "Basic xy"}
    )
    assert response.status_code == 401
    assert not vendor.calls


def test_create_user_succeeds_and_returns_the_ack(client, vendor):
    vendor.post(ADD_USER_PATH).mock(return_value=httpx.Response(201, json=ack()))

    response = client.post("/api/v1/identity/users", json=VALID_USER, headers=AUTH)

    assert response.status_code == 201
    body = response.json()
    assert body["transaction_id"] == "7345430166900109312"
    assert body["correlation_id"] == "7341821415336603648"
    assert body["timestamp"] == "2025-06-30T07:07:03.512Z"


def test_create_user_maps_our_names_to_the_vendors(client, vendor):
    route = vendor.post(ADD_USER_PATH).mock(
        return_value=httpx.Response(201, json=ack())
    )

    client.post("/api/v1/identity/users", json=VALID_USER, headers=AUTH)

    assert _sent_body(route) == {
        "userName": "pavan",
        "email": "pavan@example.com",
        "firstName": "pavan",
        "lastName": "kalyan",
        "programManagerId": "LAS",
        "groupName": "ibkart.admin",
        "mobileCountryCode": "91",
        "mobileNumber": "8374543039",
        "twoFactorAuthCodeType": "OTP",
        "twoFactorAuthCodeDeliveryChannel": "SMS",
        "password": "s3cret-pw",
    }


def test_create_user_forwards_the_dashboards_token(client, vendor):
    route = vendor.post(ADD_USER_PATH).mock(
        return_value=httpx.Response(201, json=ack())
    )
    client.post("/api/v1/identity/users", json=VALID_USER, headers=AUTH)
    assert route.calls.last.request.headers["authorization"] == AUTH["Authorization"]


def test_create_user_conflict_maps_to_409(client, vendor):
    vendor.post(ADD_USER_PATH).mock(
        return_value=httpx.Response(
            409, json=error_envelope(status=404, message="USER_NAME_ALREADY_EXISTS")
        )
    )

    response = client.post("/api/v1/identity/users", json=VALID_USER, headers=AUTH)

    # Envelope claims 404; the HTTP status is what counts.
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "USER_NAME_ALREADY_EXISTS"
    # Code not in our table yet, so it falls back to the 409 status message.
    assert error["message"] == "That already exists."


def test_create_user_vendor_401_maps_to_401(client, vendor):
    vendor.post(ADD_USER_PATH).mock(
        return_value=httpx.Response(401, json=error_envelope(status=401))
    )
    response = client.post("/api/v1/identity/users", json=VALID_USER, headers=AUTH)
    assert response.status_code == 401


def test_create_user_vendor_500_maps_to_502(client, vendor):
    vendor.post(ADD_USER_PATH).mock(
        return_value=httpx.Response(500, json=error_envelope(status=500))
    )
    response = client.post("/api/v1/identity/users", json=VALID_USER, headers=AUTH)
    assert response.status_code == 502


def test_create_user_rejects_a_bad_email_before_calling_the_vendor(client, vendor):
    bad = dict(VALID_USER, email="not-an-email")
    response = client.post("/api/v1/identity/users", json=bad, headers=AUTH)
    assert response.status_code == 422
    assert not vendor.calls


# --- forgot password ----------------------------------------------------


def test_forgot_password_requires_a_bearer_token(client, vendor):
    response = client.post(
        "/api/v1/identity/password/forgot",
        json={"user_id": "pavan", "email": "p@example.com"},
    )
    assert response.status_code == 401
    assert not vendor.calls


def test_forgot_password_succeeds(client, vendor):
    route = vendor.post(FORGOT_PATH).mock(return_value=httpx.Response(201, json=ack()))

    response = client.post(
        "/api/v1/identity/password/forgot",
        json={"user_id": "pavan", "email": "p@example.com"},
        headers=AUTH,
    )

    assert response.status_code == 202
    assert response.json()["transaction_id"] == "7345430166900109312"
    # This path takes the short program-manager spelling.
    assert _sent_body(route) == {
        "userId": "pavan",
        "programMngrId": "LAS",
        "email": "p@example.com",
    }


def test_forgot_password_unknown_user_maps_to_404(client, vendor):
    vendor.post(FORGOT_PATH).mock(
        return_value=httpx.Response(404, json=error_envelope(message="USER_NOT_FOUND"))
    )
    response = client.post(
        "/api/v1/identity/password/forgot",
        json={"user_id": "ghost", "email": "g@example.com"},
        headers=AUTH,
    )
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "USER_NOT_FOUND"
    assert error["message"] == "We could not find what you were looking for."


# --- secrets never leak -------------------------------------------------


def test_password_never_appears_in_logs(client, vendor, caplog):
    vendor.post(ADD_USER_PATH).mock(return_value=httpx.Response(201, json=ack()))

    with caplog.at_level(logging.DEBUG):
        client.post("/api/v1/identity/users", json=VALID_USER, headers=AUTH)

    everything = "\n".join(caplog.messages)
    # Guard against the assertions below passing on an empty log.
    assert "vendor_request" in everything
    assert "***REDACTED***" in everything
    assert "s3cret-pw" not in everything
    assert BD_TOKEN not in everything


def test_token_never_appears_in_logs(client, vendor, caplog):
    vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(200, json=ack(accessToken="eyJtopsecret"))
    )

    with caplog.at_level(logging.DEBUG):
        client.post("/api/v1/identity/login", json={"user_id": "las", "password": "pw"})

    everything = "\n".join(caplog.messages)
    assert "vendor_call" in everything  # the log we are inspecting really ran
    assert "eyJtopsecret" not in everything
