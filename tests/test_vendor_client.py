"""Tests for the low-level vendor wrapper: headers, normalisation, errors."""

import logging

import httpx
import pytest

from app.clients.vendor_client import VendorClient
from app.core.exceptions import (
    VendorError,
    VendorTimeoutError,
    VendorUnavailableError,
)
from tests.conftest import (
    ADD_USER_PATH,
    FORGOT_PATH,
    LOGIN_PATH,
    READ_PATH,
    ack,
    error_envelope,
)

# --- mandatory headers --------------------------------------------------


async def test_sends_the_four_mandatory_headers(vendor, vendor_client, ctx):
    route = vendor.post(LOGIN_PATH).mock(return_value=httpx.Response(200, json=ack()))

    await vendor_client.post(LOGIN_PATH, {"userId": "las"}, ctx)

    sent = route.calls.last.request.headers
    assert sent["content-type"] == "application/json"
    assert sent["x-original-forwarded-for"] == "203.0.113.7"
    assert sent["x-geolocation-latitude"] == "12.9716"
    assert sent["x-geolocation-longitude"] == "77.5946"


async def test_no_authorization_header_when_there_is_no_token(
    vendor, vendor_client, ctx
):
    route = vendor.post(LOGIN_PATH).mock(return_value=httpx.Response(200, json=ack()))
    await vendor_client.post(LOGIN_PATH, {"userId": "las"}, ctx)
    assert "authorization" not in route.calls.last.request.headers


async def test_forwards_the_users_token_as_bearer(vendor, vendor_client, authed_ctx):
    route = vendor.post(ADD_USER_PATH).mock(
        return_value=httpx.Response(201, json=ack())
    )
    await vendor_client.post(ADD_USER_PATH, {"userName": "pavan"}, authed_ctx)
    assert (
        route.calls.last.request.headers["authorization"] == "Bearer vendor.jwt.token"
    )


# --- programMngrId / programManagerId normalisation ---------------------


@pytest.mark.parametrize("path", [LOGIN_PATH, FORGOT_PATH, "/regularWallet/"])
async def test_canonical_key_is_renamed_to_the_short_spelling(
    vendor, vendor_client, ctx, path
):
    route = vendor.post(path).mock(return_value=httpx.Response(200, json=ack()))

    await vendor_client.post(path, {"programManagerId": "LAS", "userId": "las"}, ctx)

    body = route.calls.last.request.read().decode()
    assert '"programMngrId"' in body
    assert '"programManagerId"' not in body


@pytest.mark.parametrize("path", [ADD_USER_PATH, "/customermgmt/person/", READ_PATH])
async def test_canonical_key_is_left_alone_elsewhere(vendor, vendor_client, ctx, path):
    route = vendor.post(path).mock(return_value=httpx.Response(200, json=ack()))

    await vendor_client.post(path, {"programManagerId": "ibkart"}, ctx)

    body = route.calls.last.request.read().decode()
    assert '"programManagerId"' in body
    assert '"programMngrId"' not in body


async def test_short_spelling_in_a_response_is_normalised_back(
    vendor, vendor_client, ctx
):
    vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(200, json={"programMngrId": "LAS", "ok": True})
    )

    body = await vendor_client.post(LOGIN_PATH, {}, ctx)

    assert body == {"programManagerId": "LAS", "ok": True}


async def test_nested_short_spelling_is_normalised(vendor, vendor_client, ctx):
    vendor.post(READ_PATH).mock(
        return_value=httpx.Response(200, json=[{"programMngrId": "a"}])
    )
    body = await vendor_client.post(READ_PATH, {}, ctx)
    assert body == [{"programManagerId": "a"}]


# --- error envelope -----------------------------------------------------


async def test_error_envelope_is_parsed_into_all_eight_fields(
    vendor, vendor_client, ctx
):
    vendor.post(LOGIN_PATH).mock(
        return_value=httpx.Response(404, json=error_envelope())
    )

    with pytest.raises(VendorError) as caught:
        await vendor_client.post(LOGIN_PATH, {}, ctx)

    err = caught.value
    assert err.http_status == 404
    assert err.error_message == "PM_NOT_FOUND"
    assert err.error_code == "PM_NOT_FOUND"
    assert err.defined_error_code == "PM_NOT_FOUND"
    assert err.debug_message.startswith("internal stack trace")
    assert err.transaction_id == "7510946730856935424"
    assert err.correlation_id == "corr-123"
    assert err.timestamp == "30-09-2026 06:20:47"
    assert err.envelope_status == 404
    assert err.method == "POST"
    assert err.path == LOGIN_PATH


async def test_vendor_401_is_surfaced_as_401(vendor, vendor_client, ctx):
    vendor.post(ADD_USER_PATH).mock(
        return_value=httpx.Response(401, json=error_envelope(status=401))
    )

    with pytest.raises(VendorError) as caught:
        await vendor_client.post(ADD_USER_PATH, {}, ctx)

    assert caught.value.http_status == 401
    assert caught.value.dashboard_status == 401


async def test_mapping_uses_http_status_not_the_envelope_status(
    vendor, vendor_client, ctx
):
    """The docs' samples always carry status 404 whatever the real status is.

    A 409 whose envelope claims 404 must still map to 409.
    """
    vendor.post(ADD_USER_PATH).mock(
        return_value=httpx.Response(409, json=error_envelope(status=404))
    )

    with pytest.raises(VendorError) as caught:
        await vendor_client.post(ADD_USER_PATH, {}, ctx)

    assert caught.value.envelope_status == 404
    assert caught.value.http_status == 409
    assert caught.value.dashboard_status == 409


async def test_mapping_ignores_the_error_code_string(vendor, vendor_client, ctx):
    """Two different errorCode strings on the same status map identically."""
    vendor.post(ADD_USER_PATH).mock(
        return_value=httpx.Response(404, json=error_envelope(message="USER_NOT_FOUND"))
    )
    with pytest.raises(VendorError) as first:
        await vendor_client.post(ADD_USER_PATH, {}, ctx)

    vendor.post(ADD_USER_PATH).mock(
        return_value=httpx.Response(404, json=error_envelope(message="PM_NOT_FOUND"))
    )
    with pytest.raises(VendorError) as second:
        await vendor_client.post(ADD_USER_PATH, {}, ctx)

    assert first.value.dashboard_status == second.value.dashboard_status == 404


async def test_vendor_5xx_becomes_502(vendor, vendor_client, ctx):
    vendor.post(ADD_USER_PATH).mock(
        return_value=httpx.Response(500, json=error_envelope(status=500))
    )
    with pytest.raises(VendorError) as caught:
        await vendor_client.post(ADD_USER_PATH, {}, ctx)
    assert caught.value.dashboard_status == 502


async def test_non_json_error_body_still_raises_a_vendor_error(
    vendor, vendor_client, ctx
):
    vendor.post(ADD_USER_PATH).mock(
        return_value=httpx.Response(503, text="<html>gateway</html>")
    )
    with pytest.raises(VendorError) as caught:
        await vendor_client.post(ADD_USER_PATH, {}, ctx)
    assert caught.value.error_message == "Vendor request failed"
    assert caught.value.dashboard_status == 502


# --- transport failures -------------------------------------------------


async def test_timeout_becomes_vendor_timeout_error(vendor, vendor_client, ctx):
    vendor.post(ADD_USER_PATH).mock(side_effect=httpx.ConnectTimeout("too slow"))
    with pytest.raises(VendorTimeoutError) as caught:
        await vendor_client.post(ADD_USER_PATH, {}, ctx)
    assert caught.value.dashboard_status == 504


async def test_connection_failure_becomes_vendor_unavailable(
    vendor, vendor_client, ctx
):
    vendor.post(ADD_USER_PATH).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(VendorUnavailableError) as caught:
        await vendor_client.post(ADD_USER_PATH, {}, ctx)
    assert caught.value.dashboard_status == 502


# --- retry policy -------------------------------------------------------


def test_only_wallet_info_retrieval_is_retryable():
    assert VendorClient.is_retryable(READ_PATH)
    assert not VendorClient.is_retryable(LOGIN_PATH)
    assert not VendorClient.is_retryable(ADD_USER_PATH)
    assert not VendorClient.is_retryable("/customermgmt/person/")
    assert not VendorClient.is_retryable("/regularWallet/")


async def test_writes_are_never_retried(vendor, vendor_client, ctx):
    route = vendor.post(ADD_USER_PATH).mock(
        return_value=httpx.Response(500, json=error_envelope(status=500))
    )
    with pytest.raises(VendorError):
        await vendor_client.post(ADD_USER_PATH, {}, ctx)
    assert route.call_count == 1


async def test_write_timeouts_are_never_retried(vendor, vendor_client, ctx):
    route = vendor.post(ADD_USER_PATH).mock(side_effect=httpx.ConnectTimeout("slow"))
    with pytest.raises(VendorTimeoutError):
        await vendor_client.post(ADD_USER_PATH, {}, ctx)
    assert route.call_count == 1


async def test_read_only_calls_are_retried_on_5xx(vendor, vendor_client, ctx):
    route = vendor.post(READ_PATH).mock(
        return_value=httpx.Response(500, json=error_envelope(status=500))
    )
    with pytest.raises(VendorError):
        await vendor_client.post(READ_PATH, {}, ctx)
    assert route.call_count == 3


async def test_read_only_retry_succeeds_on_a_later_attempt(vendor, vendor_client, ctx):
    route = vendor.post(READ_PATH).mock(
        side_effect=[
            httpx.Response(500, json=error_envelope(status=500)),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    assert await vendor_client.post(READ_PATH, {}, ctx) == {"ok": True}
    assert route.call_count == 2


async def test_read_only_4xx_is_not_retried(vendor, vendor_client, ctx):
    """A 404 is the vendor's answer, not a blip."""
    route = vendor.post(READ_PATH).mock(
        return_value=httpx.Response(404, json=error_envelope())
    )
    with pytest.raises(VendorError):
        await vendor_client.post(READ_PATH, {}, ctx)
    assert route.call_count == 1


# --- logging ------------------------------------------------------------


async def test_logs_method_path_status_duration_and_correlation_ids(
    vendor, vendor_client, ctx, caplog
):
    vendor.post(LOGIN_PATH).mock(return_value=httpx.Response(200, json=ack()))
    with caplog.at_level(logging.INFO, logger="app.clients.vendor_client"):
        await vendor_client.post(LOGIN_PATH, {"userId": "las"}, ctx)

    line = next(m for m in caplog.messages if m.startswith("vendor_call"))
    assert "method=POST" in line
    assert f"path={LOGIN_PATH}" in line
    assert "status=200" in line
    assert "duration_ms=" in line
    assert "txn=7345430166900109312" in line
    assert "corr=7341821415336603648" in line


async def test_never_logs_a_password(vendor, vendor_client, authed_ctx, caplog):
    vendor.post(LOGIN_PATH).mock(return_value=httpx.Response(200, json=ack()))
    with caplog.at_level(logging.DEBUG, logger="app.clients.vendor_client"):
        await vendor_client.post(
            LOGIN_PATH, {"userId": "las", "password": "hunter2"}, authed_ctx
        )

    everything = "\n".join(caplog.messages)
    assert "hunter2" not in everything
    assert "***REDACTED***" in everything


async def test_never_logs_a_token(vendor, vendor_client, authed_ctx, caplog):
    vendor.post(ADD_USER_PATH).mock(
        return_value=httpx.Response(201, json=ack(accessToken="eyJsecrettokenvalue"))
    )
    with caplog.at_level(logging.DEBUG, logger="app.clients.vendor_client"):
        await vendor_client.post(
            ADD_USER_PATH, {"accessToken": "eyJanothersecret"}, authed_ctx
        )

    everything = "\n".join(caplog.messages)
    assert "vendor_request" in everything  # the log we are inspecting really ran
    assert "eyJanothersecret" not in everything
    assert "vendor.jwt.token" not in everything  # the Bearer value is never logged


async def test_a_redirect_is_not_followed_and_its_location_is_logged(
    vendor_client, vendor, ctx, caplog
):
    """Seen live 2026-10-27: POST /customermgmt/person -> 307 to the slashed path."""
    target = vendor.post("/customermgmt/person/").mock(
        return_value=httpx.Response(201, json={})
    )
    vendor.post("/customermgmt/person").mock(
        return_value=httpx.Response(307, headers={"location": "/customermgmt/person/"})
    )

    with caplog.at_level(logging.WARNING), pytest.raises(VendorError) as exc_info:
        await vendor_client.post("/customermgmt/person", {"a": 1}, ctx)

    assert exc_info.value.dashboard_status == 502
    assert not target.called
    assert "location=/customermgmt/person/" in caplog.text
