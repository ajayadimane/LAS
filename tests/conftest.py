import base64
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.clients.vendor_client import VendorCallContext, VendorClient
from app.core.config import Settings
from app.main import create_app

VENDOR_BASE = "https://vendor.test:8080"

LOGIN_PATH = "/IdentityAccessMgmt/user/login"
ADD_USER_PATH = "/IdentityAccessMgmt/user/"
FORGOT_PATH = "/IdentityAccessMgmt/user/password/forgot"
READ_PATH = "/walletInfoRetrieval/customerMgmtService/customFields"


def error_envelope(status: int = 404, message: str = "PM_NOT_FOUND"):
    """The vendor's 8-field envelope, shaped like the real thing.

    `message` is the SCREAMING_SNAKE machine code the vendor actually repeats
    in all four message fields -- not the English prose the PDFs show. Note
    `status` inside the body is whatever the vendor sends; the docs' samples
    always show 404 regardless of the real HTTP status.
    """
    return {
        "timestamp": "30-09-2026 06:20:47",
        "transactionId": "7510946730856935424",
        "correlationId": "corr-123",
        "definedErrorCode": message,
        "status": status,
        "errorCode": message,
        "errorMessage": message,
        "debugMessage": "internal stack trace that must not reach the dashboard",
    }


def make_jwt(claims: dict) -> str:
    """An unsigned JWT with these claims; the vendor verifies, we only read."""

    def seg(obj: dict) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{seg({'alg': 'RS256', 'typ': 'JWT'})}.{seg(claims)}.c2ln"


def bd_jwt(user_id: str = "97315") -> str:
    """A BD's token: groupId LAS.admin, as the sandbox issues (2026-10-28)."""
    return make_jwt({"preferred_username": user_id, "groupId": ["LAS.admin"]})


def customer_jwt(user_id: str = "9000000013") -> str:
    """A customer's token: groupId LAS.enduser."""
    return make_jwt({"preferred_username": user_id, "groupId": ["LAS.enduser"]})


def ack(**extra):
    body = {
        "transactionId": "7345430166900109312",
        "correlationId": "7341821415336603648",
        "timeStamp": "2025-06-30T07:07:03.512Z",
    }
    body.update(extra)
    return body


@pytest.fixture
def settings() -> Settings:
    return Settings(
        vendor_base_url=VENDOR_BASE,
        vendor_program_manager_id="LAS",
        default_geo_latitude="12.9716",
        default_geo_longitude="77.5946",
        vendor_retry_attempts=3,
        vendor_retry_backoff_seconds=0.0,  # keep retry tests instant
        environment="test",
        context_path="",  # tests call /api/v1/... even if .env sets one
        smtp_password=None,  # never send real mail from tests, whatever .env says
        vendor_admin_user_id="test-admin",
        vendor_admin_password="test-admin-password",
    )


@pytest.fixture
def ctx() -> VendorCallContext:
    return VendorCallContext(
        client_ip="203.0.113.7", latitude="12.9716", longitude="77.5946"
    )


@pytest.fixture
def authed_ctx() -> VendorCallContext:
    return VendorCallContext(
        client_ip="203.0.113.7",
        latitude="12.9716",
        longitude="77.5946",
        token="vendor.jwt.token",
    )


@pytest.fixture
def vendor():
    """respx router scoped to the vendor host.

    ASGI traffic to testserver is not intercepted, so TestClient works
    normally alongside this.
    """
    with respx.mock(base_url=VENDOR_BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def vendor_client(settings):
    async with httpx.AsyncClient(base_url=settings.vendor_base_url) as http:
        yield VendorClient(http, settings)


@pytest.fixture
def client(settings):
    """TestClient running the real lifespan against the test settings."""
    with TestClient(create_app(settings)) as c:
        yield c
