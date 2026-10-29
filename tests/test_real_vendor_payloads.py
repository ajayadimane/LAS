"""Regression tests built from payloads captured off the real vendor.

Every literal in this file was recorded by scripts/sandbox_smoke.py against
https://10.10.11.54:8080 on 2026-10-14, not copied from the PDFs. The PDF
samples turned out to be wrong in several ways (see api_docs/ENDPOINTS.md >
Open questions), so these pin the real behaviour.
"""

import httpx
import pytest

from app.core.exceptions import VendorError
from tests.conftest import LOGIN_PATH

# --- real captured bodies ----------------------------------------------

REAL_INVALID_CREDENTIALS = {
    "correlationId": "be924425-87ec-45d0-a4cc-b5336a814e0c",
    "debugMessage": "INVALID_CREDENTIALS_TO_AUTHENTICATE_USER",
    "definedErrorCode": "INVALID_CREDENTIALS_TO_AUTHENTICATE_USER",
    "errorCode": "INVALID_CREDENTIALS_TO_AUTHENTICATE_USER",
    "errorMessage": "INVALID_CREDENTIALS_TO_AUTHENTICATE_USER",
    "status": 400,
    "timestamp": "14-10-2026 10:03:23",
    "transactionId": "7516076178871377920",
}

REAL_PM_NOT_FOUND = {
    "correlationId": "c6a0f3b2-1d44-4f0e-9a7c-2b5e8d1f0a93",
    "debugMessage": "PM_NOT_FOUND",
    "definedErrorCode": "PM_NOT_FOUND",
    "errorCode": "PM_NOT_FOUND",
    "errorMessage": "PM_NOT_FOUND",
    "status": 404,
    "timestamp": "14-10-2026 10:03:24",
    "transactionId": "7516076178871377921",
}

REAL_PM_ID_EMPTY = {
    "correlationId": "d1e2f3a4-5b6c-7d8e-9f01-23456789abcd",
    "debugMessage": "PROGRAM_MANAGER_ID_CAN_NOT_BE_EMPTY",
    "definedErrorCode": "PROGRAM_MANAGER_ID_CAN_NOT_BE_EMPTY",
    "errorCode": "PROGRAM_MANAGER_ID_CAN_NOT_BE_EMPTY",
    "errorMessage": "PROGRAM_MANAGER_ID_CAN_NOT_BE_EMPTY",
    "status": 400,
    "timestamp": "14-10-2026 10:03:25",
    "transactionId": "7516076178871377922",
}


# --- the envelope we actually get --------------------------------------


@pytest.mark.parametrize(
    "body,http_status",
    [
        (REAL_INVALID_CREDENTIALS, 400),
        (REAL_PM_NOT_FOUND, 404),
        (REAL_PM_ID_EMPTY, 400),
    ],
)
def test_real_envelopes_have_exactly_the_eight_documented_fields(body, http_status):
    assert set(body) == {
        "timestamp",
        "transactionId",
        "correlationId",
        "definedErrorCode",
        "status",
        "errorCode",
        "errorMessage",
        "debugMessage",
    }


@pytest.mark.parametrize(
    "body,http_status",
    [
        (REAL_INVALID_CREDENTIALS, 400),
        (REAL_PM_NOT_FOUND, 404),
        (REAL_PM_ID_EMPTY, 400),
    ],
)
def test_every_real_envelope_field_is_parsed(body, http_status):
    err = VendorError.from_envelope(http_status, body)
    assert err.error_code == body["errorCode"]
    assert err.defined_error_code == body["definedErrorCode"]
    assert err.error_message == body["errorMessage"]
    assert err.debug_message == body["debugMessage"]
    assert err.transaction_id == body["transactionId"]
    assert err.correlation_id == body["correlationId"]
    assert err.timestamp == body["timestamp"]
    assert err.envelope_status == body["status"]


def test_real_error_codes_are_machine_codes_not_prose():
    """Contradicts the PDFs, which showed prose like 'The program manager not found'.

    Status-based mapping stays correct either way, but a code -> friendly
    message layer is now possible.
    """
    for body in (REAL_INVALID_CREDENTIALS, REAL_PM_NOT_FOUND, REAL_PM_ID_EMPTY):
        code = body["errorCode"]
        assert code == code.upper()
        assert " " not in code


def test_real_envelope_status_agrees_with_the_http_status():
    """Unlike every PDF sample, which hardcoded 404 regardless."""
    for body, http_status in (
        (REAL_INVALID_CREDENTIALS, 400),
        (REAL_PM_NOT_FOUND, 404),
        (REAL_PM_ID_EMPTY, 400),
    ):
        assert body["status"] == http_status


# --- mapping against real statuses -------------------------------------


async def test_bad_credentials_surface_as_400(vendor, vendor_client, ctx):
    """The login PDF lists only 200/404/500. The real answer is 400."""
    vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(400, json=REAL_INVALID_CREDENTIALS)
    )
    with pytest.raises(VendorError) as caught:
        await vendor_client.post(LOGIN_PATH, {}, ctx)
    assert caught.value.dashboard_status == 400
    assert caught.value.error_code == "INVALID_CREDENTIALS_TO_AUTHENTICATE_USER"


async def test_unknown_program_manager_surfaces_as_404(vendor, vendor_client, ctx):
    vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(404, json=REAL_PM_NOT_FOUND)
    )
    with pytest.raises(VendorError) as caught:
        await vendor_client.post(LOGIN_PATH, {}, ctx)
    assert caught.value.dashboard_status == 404


# --- the empty / non-JSON bodies the gateway really returns -------------


async def test_missing_geolocation_headers_shape_is_handled(vendor, vendor_client, ctx):
    """Real behaviour: HTTP 500 with a zero-length body, no envelope."""
    vendor.post(LOGIN_PATH).mock(return_value=httpx.Response(500, content=b""))
    with pytest.raises(VendorError) as caught:
        await vendor_client.post(LOGIN_PATH, {}, ctx)
    assert caught.value.dashboard_status == 502
    assert caught.value.error_message == "Vendor request failed"


async def test_rate_limited_empty_body_is_handled(vendor, vendor_client, ctx):
    """Real behaviour: omitting X-Original-Forwarded-For yields 429, empty body."""
    vendor.post(LOGIN_PATH).mock(return_value=httpx.Response(429, content=b""))
    with pytest.raises(VendorError) as caught:
        await vendor_client.post(LOGIN_PATH, {}, ctx)
    assert caught.value.dashboard_status == 429


async def test_gateway_plaintext_404_is_handled(vendor, vendor_client, ctx):
    """An unrouted path returns plain text '404 page not found', not JSON."""
    vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(404, text="404 page not found")
    )
    with pytest.raises(VendorError) as caught:
        await vendor_client.post(LOGIN_PATH, {}, ctx)
    assert caught.value.dashboard_status == 404
    assert caught.value.error_message == "Vendor request failed"


async def test_rate_limit_429_is_never_retried(vendor, vendor_client, ctx):
    """Retrying a throttle response would make things worse."""
    read_path = "/walletInfoRetrieval/customerMgmtService/customFields"
    route = vendor.post(read_path).mock(return_value=httpx.Response(429, content=b""))
    with pytest.raises(VendorError):
        await vendor_client.post(read_path, {}, ctx)
    assert route.call_count == 1
