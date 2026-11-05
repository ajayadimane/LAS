"""OTP verification of the mobile and email, on registering and on editing.

Both hosts are mocked: iWallet through the shared `vendor` router, the OTP
service through `otp` below. Shapes are the user's, 2026-11-04; the verify
response body was not given, so `{"success": ...}` is assumed like generate's.
"""

import json
import logging

import httpx
import pytest
import respx

from app.core.signed_tokens import sign, unsign
from tests.conftest import OTP_BASE, ack, customer_jwt, error_envelope
from tests.test_bd import AUTH, BD_ID, BY_PERSON_ID, REAL_PERSON
from tests.test_bd_onboarding import (
    ADD_USER,
    ADMIN_TOKEN,
    FORM,
    REGISTER,
    SUPERADMIN,
    WALLET,
)

GENERATE = "/ibc/v1/otp/generate"
VERIFY = "/ibc/v1/otp/verify"
MOBILE = FORM["mobile_number"]  # 9000000013
EMAIL = FORM["email_address"]


def generated(code: str = "f4b1899c663d4cd2", identifier: str = MOBILE) -> dict:
    return {
        "success": True,
        "message": "OTP sent successfully.",
        "request": {"identifier": identifier, "channel": "text"},
        "response_code": code,
    }


@pytest.fixture
def settings(settings):
    return settings.model_copy(update={"otp_required": True})


@pytest.fixture
def otp():
    with respx.mock(base_url=OTP_BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
def not_registered(vendor):
    return vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(404, json=error_envelope(404, "PERSON_NOT_FOUND"))
    )


def _body(route) -> dict:
    return json.loads(route.calls.last.request.read())


def send(
    client,
    channel="mobile",
    value=MOBILE,
    purpose="register",
    person_id=MOBILE,
    headers=AUTH,
):
    return client.post(
        "/api/v1/otp/send",
        json={
            "channel": channel,
            "value": value,
            "purpose": purpose,
            "person_id": person_id,
        },
        headers=headers,
    )


def verify(client, session, code="861283", headers=AUTH):
    return client.post(
        "/api/v1/otp/verify",
        json={"otp_session": session, "otp": code},
        headers=headers,
    )


def verified_token(client, otp, channel, value, purpose="register", person_id=MOBILE):
    """The whole send -> verify flow, returning the proof."""
    otp.post(GENERATE).mock(
        return_value=httpx.Response(
            200, json=generated(code=f"rc-{channel}", identifier=value)
        )
    )
    otp.post(VERIFY).mock(return_value=httpx.Response(200, json={"success": True}))
    sent = send(client, channel, value, purpose, person_id)
    assert sent.status_code == 200, sent.text
    checked = verify(client, sent.json()["otp_session"])
    assert checked.status_code == 200, checked.text
    return checked.json()["verification_token"]


# -- settings ----------------------------------------------------------------


def test_settings_tell_the_dashboard_the_rules(client):
    response = client.get("/api/v1/otp/settings")

    assert response.json() == {
        "required": True,
        "code_length": 6,
        "max_attempts": 3,
        "resend_after_seconds": 30,
    }


# -- send ----------------------------------------------------------------------


def test_sends_an_sms_with_the_api_key(client, otp, not_registered):
    route = otp.post(GENERATE).mock(return_value=httpx.Response(200, json=generated()))

    response = send(client)

    assert response.status_code == 200
    assert _body(route) == {"identifier": MOBILE, "channel": "text"}
    assert route.calls.last.request.headers["api-key"] == "test-otp-key"
    assert "authorization" not in route.calls.last.request.headers
    body = response.json()
    assert body["sent_to"] == "******0013"
    assert body["max_attempts"] == 3
    # The service's response_code is inside our signed session, never bare.
    assert "f4b1899c663d4cd2" not in json.dumps(body)


def test_sends_an_email_otp_lowercased(client, otp, not_registered):
    route = otp.post(GENERATE).mock(return_value=httpx.Response(200, json=generated()))

    response = send(client, "email", " Kunal@Example.COM ")

    assert response.status_code == 200
    assert _body(route) == {"identifier": "kunal@example.com", "channel": "email"}
    assert response.json()["sent_to"] == "ku***@example.com"


def test_an_already_registered_mobile_gets_no_otp(client, vendor, otp):
    vendor.post(BY_PERSON_ID).mock(return_value=httpx.Response(200, json=REAL_PERSON))
    route = otp.post(GENERATE).mock(return_value=httpx.Response(200, json=generated()))

    response = send(client)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CUSTOMER_ALREADY_REGISTERED"
    assert not route.called


def test_a_forged_token_sends_nothing(client, vendor, otp):
    """The vendor rejects the token; we must not have sent an SMS first."""
    vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(401, json=error_envelope(401, "UNAUTHORIZED"))
    )
    route = otp.post(GENERATE).mock(return_value=httpx.Response(200, json=generated()))

    assert send(client).status_code == 401
    assert not route.called


def test_only_a_bd_registers(client, otp):
    response = send(client, headers={"Authorization": f"Bearer {customer_jwt()}"})

    assert response.status_code == 403


def test_the_mobile_verified_must_be_the_one_registered(client, otp):
    response = send(client, value="9111111111")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "OTP_VALUE_MISMATCH"


def test_a_bad_mobile_is_refused(client, otp):
    response = send(client, value="12345", person_id="12345")

    assert response.status_code == 422


def test_resending_too_soon_is_refused(client, otp, not_registered):
    route = otp.post(GENERATE).mock(return_value=httpx.Response(200, json=generated()))

    assert send(client).status_code == 200
    again = send(client)

    assert again.status_code == 429
    assert again.json()["error"]["code"] == "OTP_RESEND_TOO_SOON"
    assert route.call_count == 1


def test_a_refused_send_is_a_502(client, otp, not_registered):
    otp.post(GENERATE).mock(
        return_value=httpx.Response(200, json={"success": False, "message": "nope"})
    )

    response = send(client)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "OTP_SEND_FAILED"


def test_a_send_is_never_retried(client, otp, not_registered):
    route = otp.post(GENERATE).mock(return_value=httpx.Response(503))

    assert send(client).status_code == 502
    assert route.call_count == 1


def test_unconfigured_otp_is_a_503(client, otp, settings):
    settings.otp_api_key = None

    response = send(client)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "OTP_NOT_CONFIGURED"


# -- verify --------------------------------------------------------------------


@pytest.fixture
def session(client, otp, not_registered):
    otp.post(GENERATE).mock(return_value=httpx.Response(200, json=generated()))
    return send(client).json()["otp_session"]


def test_a_right_code_returns_a_proof(client, otp, session):
    route = otp.post(VERIFY).mock(
        return_value=httpx.Response(200, json={"success": True})
    )

    response = verify(client, session)

    assert response.status_code == 200
    assert _body(route) == {"response_code": "f4b1899c663d4cd2", "otp": "861283"}
    assert route.calls.last.request.headers["api-key"] == "test-otp-key"
    body = response.json()
    assert (body["channel"], body["value"]) == ("mobile", MOBILE)


def test_three_wrong_codes_end_the_session(client, otp, session):
    route = otp.post(VERIFY).mock(
        return_value=httpx.Response(400, json={"success": False, "message": "Invalid"})
    )

    first = verify(client, session)
    second = verify(client, session)
    third = verify(client, session)
    fourth = verify(client, session)

    assert (
        first.json()["error"]["message"] == "Incorrect OTP. You have 2 attempts left."
    )
    assert (
        second.json()["error"]["message"] == "Incorrect OTP. You have 1 attempt left."
    )
    assert third.json()["error"]["code"] == "OTP_ATTEMPTS_EXCEEDED"
    assert fourth.json()["error"]["code"] == "OTP_ATTEMPTS_EXCEEDED"
    # The fourth never reached the service.
    assert route.call_count == 3


def test_a_200_saying_no_is_a_wrong_code(client, otp, session):
    otp.post(VERIFY).mock(return_value=httpx.Response(200, json={"success": False}))

    response = verify(client, session)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "OTP_INVALID"


def test_a_service_failure_costs_no_attempt(client, otp, session):
    route = otp.post(VERIFY).mock(
        side_effect=[httpx.Response(500), httpx.Response(200, json={"success": True})]
    )

    assert verify(client, session).status_code == 502
    assert verify(client, session).status_code == 200
    assert route.call_count == 2


def test_a_code_proves_once(client, otp, session):
    otp.post(VERIFY).mock(return_value=httpx.Response(200, json={"success": True}))

    assert verify(client, session).status_code == 200
    again = verify(client, session)

    assert again.json()["error"]["code"] == "OTP_ALREADY_USED"


def test_another_users_session_is_refused(client, otp, session):
    other = {"Authorization": f"Bearer {customer_jwt()}"}

    response = verify(client, session, headers=other)

    assert response.json()["error"]["code"] == "OTP_SESSION_EXPIRED"


def test_a_tampered_session_is_refused(client, otp, session):
    body, mac = session.split(".")
    response = verify(client, f"{body}x.{mac}")

    assert response.json()["error"]["code"] == "OTP_SESSION_EXPIRED"


def test_neither_the_code_nor_the_key_is_logged(client, otp, session, caplog):
    otp.post(VERIFY).mock(return_value=httpx.Response(200, json={"success": True}))

    with caplog.at_level(logging.DEBUG):
        verify(client, session, code="861283")

    assert "861283" not in caplog.text
    assert "test-otp-key" not in caplog.text
    assert "f4b1899c663d4cd2" not in caplog.text


# -- registering ---------------------------------------------------------------


@pytest.fixture
def onboarding(vendor):
    vendor.post(REGISTER).mock(return_value=httpx.Response(201, json=ack()))
    vendor.post(WALLET).mock(return_value=httpx.Response(201, json=ack()))
    vendor.post(SUPERADMIN).mock(
        return_value=httpx.Response(200, json=ack(accessToken=ADMIN_TOKEN))
    )
    vendor.post(ADD_USER).mock(return_value=httpx.Response(201, json=ack()))


def test_registers_with_both_verified(client, otp, not_registered, onboarding):
    mobile = verified_token(client, otp, "mobile", MOBILE)
    email = verified_token(client, otp, "email", EMAIL)

    response = client.post(
        "/api/v1/bd/customers",
        json={**FORM, "mobile_otp_token": mobile, "email_otp_token": email},
        headers=AUTH,
    )

    assert response.status_code == 201, response.text


def test_registering_unverified_is_refused(client, vendor, otp):
    register = vendor.post(REGISTER).mock(return_value=httpx.Response(201, json=ack()))

    response = client.post("/api/v1/bd/customers", json=FORM, headers=AUTH)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "OTP_VERIFICATION_REQUIRED"
    assert not register.called


def test_a_proof_for_another_email_is_refused(client, vendor, otp, not_registered):
    mobile = verified_token(client, otp, "mobile", MOBILE)
    email = verified_token(client, otp, "email", "someone.else@example.com")
    register = vendor.post(REGISTER).mock(return_value=httpx.Response(201, json=ack()))

    response = client.post(
        "/api/v1/bd/customers",
        json={**FORM, "mobile_otp_token": mobile, "email_otp_token": email},
        headers=AUTH,
    )

    assert response.status_code == 400
    assert "email" in response.json()["error"]["message"]
    assert not register.called


def test_the_mobile_proof_cannot_stand_in_for_the_email(
    client, vendor, otp, not_registered
):
    mobile = verified_token(client, otp, "mobile", MOBILE)

    response = client.post(
        "/api/v1/bd/customers",
        json={**FORM, "mobile_otp_token": mobile, "email_otp_token": mobile},
        headers=AUTH,
    )

    assert response.status_code == 400


# -- editing -------------------------------------------------------------------


EDIT = f"/api/v1/bd/customers/{MOBILE}/email"
NEW_EMAIL = "new@example.com"


@pytest.fixture
def owned(vendor):
    return vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(200, json=REAL_PERSON)
    )


def test_changes_a_verified_email(client, vendor, otp, owned):
    token = verified_token(client, otp, "email", NEW_EMAIL, "update", MOBILE)
    route = vendor.put("/customermgmt/person/emailAddress").mock(
        return_value=httpx.Response(200, json=ack())
    )

    response = client.put(
        EDIT, json={"email_address": NEW_EMAIL, "email_otp_token": token}, headers=AUTH
    )

    assert response.status_code == 200
    assert _body(route)["emailAddress"] == NEW_EMAIL


def test_changing_to_an_unverified_email_is_refused(client, vendor, otp, owned):
    route = vendor.put("/customermgmt/person/emailAddress").mock(
        return_value=httpx.Response(200, json=ack())
    )

    response = client.put(EDIT, json={"email_address": NEW_EMAIL}, headers=AUTH)

    assert response.status_code == 400
    assert not route.called


def test_a_register_proof_does_not_work_for_an_edit(
    client, vendor, otp, not_registered
):
    token = verified_token(client, otp, "email", NEW_EMAIL, "register", MOBILE)

    response = client.put(
        EDIT, json={"email_address": NEW_EMAIL, "email_otp_token": token}, headers=AUTH
    )

    assert response.status_code == 400


def test_a_bd_cannot_verify_for_another_bds_customer(client, vendor, otp):
    vendor.post(BY_PERSON_ID).mock(
        return_value=httpx.Response(200, json={**REAL_PERSON, "brokerId.id": "11111"})
    )
    route = otp.post(GENERATE).mock(return_value=httpx.Response(200, json=generated()))

    response = send(client, "email", NEW_EMAIL, "update", MOBILE)

    assert response.status_code == 404
    assert not route.called


def test_a_customer_verifies_their_own_details(client, vendor, otp, owned):
    me = {"Authorization": f"Bearer {customer_jwt(MOBILE)}"}
    otp.post(GENERATE).mock(return_value=httpx.Response(200, json=generated()))

    assert (
        send(client, "email", NEW_EMAIL, "update", MOBILE, headers=me).status_code
        == 200
    )
    other = send(client, "email", NEW_EMAIL, "update", "9222222222", headers=me)
    assert other.status_code == 404


# -- signed tokens -------------------------------------------------------------


def test_tokens_expire_and_keep_their_type():
    secret = b"k"
    token = sign({"a": 1}, secret, "x", ttl_seconds=60)

    assert unsign(token, secret, "x")["a"] == 1
    assert unsign(token, secret, "y") is None
    assert unsign(token, b"other", "x") is None
    assert unsign(sign({"a": 1}, secret, "x", ttl_seconds=-1), secret, "x") is None
    assert unsign("garbage", secret, "x") is None
    assert unsign(None, secret, "x") is None


def test_bd_id_constant_is_the_one_tested():
    # Guards the fixtures above: AUTH is this BD, owner of REAL_PERSON.
    assert REAL_PERSON["brokerId.id"] == BD_ID


def test_a_mobile_held_by_another_person_is_explained(client, vendor, settings):
    """Seen live 2026-11-04: the OTP step passed (no person with this id), but
    another person holds the mobile, and registration says so."""
    settings.otp_required = False
    vendor.post(REGISTER).mock(
        return_value=httpx.Response(
            400, json=error_envelope(400, "MOBILE_NUMBER_ALREADY_EXISTS")
        )
    )

    response = client.post("/api/v1/bd/customers", json=FORM, headers=AUTH)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "MOBILE_NUMBER_ALREADY_EXISTS"
    assert error["message"].startswith("This mobile number is already registered")
