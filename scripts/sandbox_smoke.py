#!/usr/bin/env python
"""Call the real vendor login and report exactly what comes back.

Credentials are never stored here. They are read from .env:

    VENDOR_SMOKE_USER_ID=...
    VENDOR_SMOKE_PASSWORD=...
    VENDOR_SMOKE_PROGRAM_MANAGER_ID=...   # optional, falls back to settings

Usage:
    python scripts/sandbox_smoke.py              # correct-password login
    python scripts/sandbox_smoke.py --wrong      # deliberate bad password
    python scripts/sandbox_smoke.py --both       # both, in order

The access token is redacted everywhere. What is printed about it is derived
metadata only (length, JWT header alg/typ), never the signature or payload.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path
from typing import Any

# Allow running as a plain script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from pydantic import SecretStr  # noqa: E402
from pydantic_settings import BaseSettings, SettingsConfigDict  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.exceptions import VendorError  # noqa: E402
from app.core.logging import redact  # noqa: E402
from app.schemas.identity import LoginResponse  # noqa: E402
from app.services.identity import LOGIN_PATH  # noqa: E402

# Field names the PDFs promise. Used to diff reality against the docs.
DOCUMENTED_SUCCESS_FIELDS = {
    "transactionId",
    "correlationId",
    "timeStamp",
    "accessToken",
}
DOCUMENTED_ERROR_FIELDS = {
    "timestamp",
    "transactionId",
    "correlationId",
    "definedErrorCode",
    "status",
    "errorCode",
    "errorMessage",
    "debugMessage",
}

SENSITIVE_HEADERS = {"authorization", "set-cookie", "cookie", "proxy-authorization"}

# Vendor envelope field -> the VendorError attribute it should land in.
ENVELOPE_TO_ATTR = {
    "timestamp": "timestamp",
    "transactionId": "transaction_id",
    "correlationId": "correlation_id",
    "definedErrorCode": "defined_error_code",
    "status": "envelope_status",
    "errorCode": "error_code",
    "errorMessage": "error_message",
    "debugMessage": "debug_message",
}


class SmokeSettings(BaseSettings):
    """Credentials, read from .env only. Never hardcode these."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    vendor_smoke_user_id: str | None = None
    vendor_smoke_password: SecretStr | None = None
    vendor_smoke_program_manager_id: str | None = None
    # Sent as X-Original-Forwarded-For. The vendor requires the header.
    vendor_smoke_client_ip: str = "127.0.0.1"


def _rule(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def _redact_headers(headers: httpx.Headers) -> dict:
    return {
        k: ("***REDACTED***" if k.lower() in SENSITIVE_HEADERS else v)
        for k, v in headers.items()
    }


def _token_metadata(token: str) -> dict:
    """Non-secret facts about the token. Never returns any part of its value."""
    info: dict[str, Any] = {"length": len(token), "segments": token.count(".") + 1}
    try:
        header_b64 = token.split(".")[0]
        header_b64 += "=" * (-len(header_b64) % 4)
        header = json.loads(base64.urlsafe_b64decode(header_b64))
        info["jwt_header"] = header  # {"alg": ..., "typ": ...} - not secret
    except Exception:
        info["jwt_header"] = "could not decode (not a JWT?)"
    return info


async def call_login(
    user_id: str, password: str, program_manager_id: str, client_ip: str
) -> httpx.Response | None:
    settings = get_settings()

    # Built here rather than via VendorClient so the raw status, headers and
    # body are visible before any of our parsing touches them.
    body = {
        "userId": user_id,
        "programMngrId": program_manager_id,  # this path uses the short spelling
        "password": password,
    }
    headers = {
        "Content-Type": "application/json",
        "X-Original-Forwarded-For": client_ip,
        "X-Geolocation-Latitude": settings.default_geo_latitude,
        "X-Geolocation-Longitude": settings.default_geo_longitude,
    }

    url = f"{settings.vendor_base_url}{LOGIN_PATH}"
    print(f"POST {url}")
    print(f"  verify_ssl = {settings.vendor_verify_ssl}")
    print(f"  timeout    = {settings.vendor_timeout_seconds}s")
    print(f"  request body (redacted): {redact(body)}")

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                settings.vendor_timeout_seconds,
                connect=settings.vendor_connect_timeout_seconds,
            ),
            verify=settings.vendor_verify_ssl,
        ) as client:
            return await client.post(url, json=body, headers=headers)
    except httpx.TimeoutException as exc:
        print(f"\n  TIMEOUT after {settings.vendor_timeout_seconds}s: {exc!r}")
    except httpx.TransportError as exc:
        print(f"\n  TRANSPORT ERROR: {type(exc).__name__}: {exc}")
        print("  The vendor host is an RFC-1918 address. Check you are on a")
        print("  network that can reach it, and whether its TLS certificate is")
        print("  self-signed (then set VENDOR_VERIFY_SSL=false in .env).")
    return None


def report_response(response: httpx.Response, *, expect_success: bool) -> None:
    _rule("RAW RESPONSE")
    print(f"HTTP status : {response.status_code} {response.reason_phrase}")
    print(f"Elapsed     : {response.elapsed.total_seconds():.3f}s")
    print("Headers:")
    for k, v in _redact_headers(response.headers).items():
        print(f"  {k}: {v}")

    try:
        body = response.json()
    except ValueError:
        print("\nBody (not JSON):")
        print(f"  {response.text[:2000]}")
        return

    print("\nBody (token redacted):")
    print(json.dumps(redact(body), indent=2, sort_keys=True))

    if isinstance(body, dict) and isinstance(body.get("accessToken"), str):
        print("\nToken metadata (derived, no secret material):")
        for k, v in _token_metadata(body["accessToken"]).items():
            print(f"  {k}: {v}")

    if expect_success and response.is_success:
        compare_success(body)
    else:
        compare_error(response.status_code, body)


def compare_success(body: Any) -> None:
    _rule("COMPARISON: real success body vs our LoginResponse schema")
    if not isinstance(body, dict):
        print(f"  Body is {type(body).__name__}, expected an object. Cannot compare.")
        return

    actual = set(body)
    print(f"  documented fields : {sorted(DOCUMENTED_SUCCESS_FIELDS)}")
    print(f"  actual fields     : {sorted(actual)}")
    missing = DOCUMENTED_SUCCESS_FIELDS - actual
    extra = actual - DOCUMENTED_SUCCESS_FIELDS
    print(f"  MISSING vs docs   : {sorted(missing) or 'none'}")
    print(f"  EXTRA vs docs     : {sorted(extra) or 'none'}")

    print("\n  Mapping into LoginResponse:")
    token = body.get("accessToken")
    if not token:
        print("    FAIL: no accessToken -> IdentityService raises VendorError(502)")
        return
    try:
        parsed = LoginResponse(
            access_token=token,
            transaction_id=body.get("transactionId"),
            correlation_id=body.get("correlationId"),
        )
    except Exception as exc:
        print(f"    FAIL: LoginResponse rejected the real data: {exc}")
        return
    shown = parsed.model_dump()
    shown["access_token"] = "***REDACTED***"
    print(f"    OK: {shown}")
    if parsed.expires_in is None:
        print(
            "    NOTE: expires_in is null. If the real body carries any TTL "
            "field (see EXTRA above), the TODO(token-expiry) can be closed."
        )


def compare_error(status_code: int, body: Any) -> None:
    _rule("COMPARISON: real error body vs the documented 8-field envelope")
    if not isinstance(body, dict):
        print(f"  Body is {type(body).__name__}, not an object.")
        print("  VendorError.from_envelope falls back to a bare error:")
        err = VendorError.from_envelope(
            status_code, body, method="POST", path=LOGIN_PATH
        )
        print(
            f"    message={err.error_message!r} dashboard_status={err.dashboard_status}"
        )
        return

    actual = set(body)
    missing = DOCUMENTED_ERROR_FIELDS - actual
    extra = actual - DOCUMENTED_ERROR_FIELDS
    print(f"  documented fields : {sorted(DOCUMENTED_ERROR_FIELDS)}")
    print(f"  actual fields     : {sorted(actual)}")
    print(f"  MISSING vs docs   : {sorted(missing) or 'none'}")
    print(f"  EXTRA vs docs     : {sorted(extra) or 'none'}")

    envelope_status = body.get("status")
    print(f"\n  HTTP status       : {status_code}")
    print(f"  envelope 'status' : {envelope_status}")
    if envelope_status != status_code:
        print("  -> They disagree. Our mapping keys on the HTTP status, so this")
        print("     is handled correctly, but it confirms the envelope's own")
        print("     status field is not trustworthy.")

    err = VendorError.from_envelope(status_code, body, method="POST", path=LOGIN_PATH)
    print("\n  Parsed into VendorError:")
    for field in (
        "http_status",
        "envelope_status",
        "error_code",
        "defined_error_code",
        "error_message",
        "transaction_id",
        "correlation_id",
        "timestamp",
    ):
        print(f"    {field:18} = {getattr(err, field)!r}")
    print(f"    {'debug_message':18} = {err.debug_message!r}  (logged, never returned)")
    print(f"\n  -> dashboard sees HTTP {err.dashboard_status}")
    unparsed = [
        field
        for field, attr in ENVELOPE_TO_ATTR.items()
        if field in actual and getattr(err, attr) is None
    ]
    if unparsed:
        print(f"  WARNING: present in the body but parsed as None: {sorted(unparsed)}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wrong", action="store_true", help="send a deliberately wrong password"
    )
    parser.add_argument(
        "--both",
        action="store_true",
        help="run the correct login, then a wrong-password login",
    )
    args = parser.parse_args()

    creds = SmokeSettings()
    settings = get_settings()

    if not creds.vendor_smoke_user_id or not creds.vendor_smoke_password:
        print("Missing credentials. Add to .env (and never to this script):\n")
        print("  VENDOR_SMOKE_USER_ID=<user>")
        print("  VENDOR_SMOKE_PASSWORD=<password>")
        print("  VENDOR_SMOKE_PROGRAM_MANAGER_ID=<pm>   # optional")
        print(f"\nCurrent vendor base URL: {settings.vendor_base_url}")
        return 2

    pm_id = creds.vendor_smoke_program_manager_id or settings.vendor_program_manager_id
    runs = []
    if args.both:
        runs = [("CORRECT PASSWORD", False), ("WRONG PASSWORD", True)]
    else:
        runs = [("WRONG PASSWORD" if args.wrong else "CORRECT PASSWORD", args.wrong)]

    exit_code = 0
    for title, use_wrong in runs:
        _rule(f"LOGIN ATTEMPT: {title}")
        password = (
            "definitely-not-the-password-" + "x" * 12
            if use_wrong
            else creds.vendor_smoke_password.get_secret_value()
        )
        response = await call_login(
            creds.vendor_smoke_user_id, password, pm_id, creds.vendor_smoke_client_ip
        )
        if response is None:
            exit_code = 1
            continue
        report_response(response, expect_success=not use_wrong)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
