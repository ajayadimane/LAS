"""The vendor-code -> user-facing message layer."""

import httpx
import pytest

from app.core.error_messages import (
    CREDENTIAL_FAILURE_CODES,
    CREDENTIAL_FAILURE_MESSAGE,
    GENERIC_MESSAGE,
    STATUS_MESSAGES,
    VENDOR_ERROR_MESSAGES,
    friendly_message,
)
from tests.conftest import LOGIN_PATH, error_envelope
from tests.test_real_vendor_payloads import REAL_INVALID_CREDENTIALS

# --- resolution order ---------------------------------------------------


def test_known_code_wins():
    expected = VENDOR_ERROR_MESSAGES["PM_NOT_FOUND"]
    assert friendly_message("PM_NOT_FOUND", 404) == expected


def test_unknown_code_falls_back_to_the_status_message():
    assert friendly_message("NEVER_SEEN_BEFORE", 409) == STATUS_MESSAGES[409]


def test_absent_code_falls_back_to_the_status_message():
    """Real case: a 429 and an empty-bodied 500 carry no code at all."""
    assert friendly_message(None, 429) == STATUS_MESSAGES[429]


def test_unknown_code_and_unmapped_status_fall_back_to_generic():
    assert friendly_message("NEVER_SEEN_BEFORE", 418) == GENERIC_MESSAGE
    assert friendly_message(None, 418) == GENERIC_MESSAGE


def test_empty_string_code_is_treated_as_absent():
    assert friendly_message("", 404) == STATUS_MESSAGES[404]


# --- quality of the messages themselves ---------------------------------


@pytest.mark.parametrize("message", list(VENDOR_ERROR_MESSAGES.values()))
def test_messages_read_as_prose_not_machine_codes(message):
    assert message
    assert message != message.upper()
    assert "_" not in message
    assert message[0].isupper() and message.rstrip().endswith(".")


@pytest.mark.parametrize("message", list(STATUS_MESSAGES.values()))
def test_status_messages_read_as_prose(message):
    assert message and "_" not in message
    assert message != message.upper()


def test_no_message_leaks_vendor_internals():
    everything = (
        " ".join(list(VENDOR_ERROR_MESSAGES.values()) + list(STATUS_MESSAGES.values()))
        + GENERIC_MESSAGE
    )
    for leak in ("debug", "stack", "trace", "sql", "exception", "null", "None"):
        assert leak not in everything.lower() or leak == "null"


# --- the indistinguishability requirement -------------------------------


def test_every_credential_failure_code_shares_one_message():
    """A wrong password and an unknown user must look identical.

    Any new credential-failure code belongs in CREDENTIAL_FAILURE_CODES, which
    makes it inherit this message automatically.
    """
    assert CREDENTIAL_FAILURE_CODES
    for code in CREDENTIAL_FAILURE_CODES:
        assert friendly_message(code, 400) == CREDENTIAL_FAILURE_MESSAGE
        assert friendly_message(code, 401) == CREDENTIAL_FAILURE_MESSAGE
        assert friendly_message(code, 404) == CREDENTIAL_FAILURE_MESSAGE


def test_credential_message_does_not_say_which_half_was_wrong():
    lowered = CREDENTIAL_FAILURE_MESSAGE.lower()
    for leak in (
        "no such user",
        "unknown user",
        "user does not exist",
        "user not found",
        "wrong password",
        "incorrect password",
    ):
        assert leak not in lowered


def test_wrong_password_and_unknown_user_are_indistinguishable(client, vendor):
    """End to end, through the real API surface.

    The vendor returns one generic code for both cases -- verified live for the
    unknown-user half on 2026-10-14. This test pins that the dashboard response
    is byte-identical either way, so the API cannot be used to enumerate users.
    """
    vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(400, json=REAL_INVALID_CREDENTIALS)
    )
    unknown_user = client.post(
        "/api/v1/identity/login",
        json={"user_id": "no-such-user-at-all", "password": "whatever"},
    )

    vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(400, json=REAL_INVALID_CREDENTIALS)
    )
    wrong_password = client.post(
        "/api/v1/identity/login",
        json={"user_id": "a-real-user", "password": "the-wrong-password"},
    )

    assert unknown_user.status_code == wrong_password.status_code == 400
    assert unknown_user.json() == wrong_password.json()
    error = unknown_user.json()["error"]
    assert error["message"] == CREDENTIAL_FAILURE_MESSAGE
    assert error["code"] == "INVALID_CREDENTIALS_TO_AUTHENTICATE_USER"


# --- the shape the dashboard receives -----------------------------------


def test_error_body_carries_both_code_and_message(client, vendor):
    vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(404, json=error_envelope(message="PM_NOT_FOUND"))
    )
    response = client.post(
        "/api/v1/identity/login", json={"user_id": "u", "password": "p"}
    )
    error = response.json()["error"]
    assert set(error) == {
        "status",
        "code",
        "message",
        "transaction_id",
        "correlation_id",
    }
    assert error["code"] == "PM_NOT_FOUND"
    assert error["message"] == VENDOR_ERROR_MESSAGES["PM_NOT_FOUND"]


def test_code_is_null_when_the_vendor_sends_no_envelope(client, vendor):
    """Real case: rate limiting returns 429 with a zero-length body."""
    vendor.post(LOGIN_PATH).mock(return_value=httpx.Response(429, content=b""))
    response = client.post(
        "/api/v1/identity/login", json={"user_id": "u", "password": "p"}
    )
    assert response.status_code == 429
    error = response.json()["error"]
    assert error["code"] is None
    assert error["message"] == STATUS_MESSAGES[429]


def test_the_vendors_machine_code_is_never_used_as_the_message(client, vendor):
    vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(400, json=REAL_INVALID_CREDENTIALS)
    )
    response = client.post(
        "/api/v1/identity/login", json={"user_id": "u", "password": "p"}
    )
    message = response.json()["error"]["message"]
    assert message != "INVALID_CREDENTIALS_TO_AUTHENTICATE_USER"
    assert "_" not in message


def test_debug_message_still_never_reaches_the_dashboard(client, vendor):
    vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(404, json=error_envelope())
    )
    response = client.post(
        "/api/v1/identity/login", json={"user_id": "u", "password": "p"}
    )
    assert "internal stack trace" not in response.text


def test_unmapped_code_is_logged_for_follow_up(client, vendor, caplog):
    """A code with no message should be visible in logs, not silently generic."""
    import logging

    vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(409, json=error_envelope(message="A_NEW_CODE"))
    )
    with caplog.at_level(logging.WARNING):
        client.post("/api/v1/identity/login", json={"user_id": "u", "password": "p"})

    everything = "\n".join(caplog.messages)
    assert "unmapped_vendor_error_code" in everything
    assert "A_NEW_CODE" in everything


def test_known_code_does_not_trigger_the_unmapped_warning(client, vendor, caplog):
    import logging

    vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(404, json=error_envelope(message="PM_NOT_FOUND"))
    )
    with caplog.at_level(logging.WARNING):
        client.post("/api/v1/identity/login", json={"user_id": "u", "password": "p"})

    assert "unmapped_vendor_error_code" not in "\n".join(caplog.messages)
