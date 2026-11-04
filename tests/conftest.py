import base64
import json
import os
import socket
from pathlib import Path

import httpx
import pytest
import respx
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.clients.vendor_client import VendorCallContext, VendorClient
from app.core.config import Settings
from app.main import create_app
from app.services.dpr_agentic.jobs import engine_for
from app.services.dpr_agentic.migrate import apply_migrations

VENDOR_BASE = "https://vendor.test:8080"


# --- Test isolation: never the real .env, never a real outbound call -------
#
# Disclosed incident (PROGRESS.md milestone 5): an ad hoc Settings(...) call
# in a test didn't explicitly null dpr_agentic_gemini_api_key, Settings reads
# the real .env for any field not explicitly passed, and that machine's real
# .env has a real, working Gemini key -- so that test made several real,
# billed calls to generativelanguage.googleapis.com. These three fixtures
# close that class of mistake repo-wide, not just in the one file it
# happened in, and are intentionally layered (any one of them catching the
# next test like that is enough; all three together is the point):
#
#   1. _never_read_the_real_dotenv_in_tests: Settings(...) can no longer
#      read .env at all during tests, so an unset secret field becomes None/
#      default, never a real value, regardless of which fields a test
#      happens to override explicitly.
#   2. _block_real_outbound_network_in_tests: any real TCP connection
#      attempt other than to the throwaway Postgres (DPR_TEST_DATABASE_URL,
#      always loopback) raises immediately. respx-mocked vendor calls and
#      TestClient's in-process ASGI transport never reach a real socket in
#      the first place, so neither is affected.
#   3. _fail_on_real_gemini_client: a belt-and-braces check specific to the
#      exact incident -- constructing GeminiAgentClient with the literal key
#      from the real on-disk .env (read once here for comparison only, never
#      used to configure anything) fails immediately, with a message naming
#      Gemini specifically, even if something bypassed Settings entirely
#      (e.g. read os.environ directly).
#
# All three are session-scoped and autouse: every test in the suite gets
# them, with no per-test opt-in to forget.

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@pytest.fixture(autouse=True, scope="session")
def _never_read_the_real_dotenv_in_tests():
    original_config = Settings.model_config
    Settings.model_config = {**original_config, "env_file": None}
    try:
        yield
    finally:
        Settings.model_config = original_config


@pytest.fixture(autouse=True, scope="session")
def _block_real_outbound_network_in_tests():
    real_connect = socket.socket.connect

    def guarded_connect(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else None
        if host is None or host in _LOOPBACK_HOSTS:
            return real_connect(self, address, *args, **kwargs)
        raise RuntimeError(
            f"Outbound network connection to {address!r} blocked during "
            "tests (tests/conftest.py's _block_real_outbound_network_in_"
            "tests) -- mock it instead: respx for vendor calls, a fake/stub "
            "client for Gemini. Only loopback (the throwaway test Postgres) "
            "is allowed. See PROGRESS.md milestone 5's disclosed incident "
            "for why this exists."
        )

    socket.socket.connect = guarded_connect
    try:
        yield
    finally:
        socket.socket.connect = real_connect


@pytest.fixture(autouse=True, scope="session")
def _fail_on_real_gemini_client():
    from app.services.dpr_agentic.gemini import GeminiAgentClient

    env_path = _REPO_ROOT / ".env"
    real_key = (
        dotenv_values(env_path).get("DPR_AGENTIC_GEMINI_API_KEY")
        if env_path.exists()
        else None
    )
    original_init = GeminiAgentClient.__init__

    def guarded_init(self, client, api_key, model):
        if real_key and api_key == real_key:
            raise RuntimeError(
                "A test constructed GeminiAgentClient with the real "
                "DPR_AGENTIC_GEMINI_API_KEY from .env -- this would make "
                "real, possibly billed calls to the Gemini API. Pass an "
                "explicit fake key (e.g. 'test-gemini-key') instead. See "
                "PROGRESS.md milestone 5's disclosed incident."
            )
        return original_init(self, client, api_key, model)

    GeminiAgentClient.__init__ = guarded_init
    try:
        yield
    finally:
        GeminiAgentClient.__init__ = original_init


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
        # Off by default here regardless of .env -- tests that need it on
        # (the dpr_settings/dpr_client fixtures below) opt in explicitly.
        dpr_agentic_enabled=False,
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


# --- DPR durable-job Postgres fixtures ----------------------------------
#
# jobs.py / branch_status.py use Postgres-specific SQL (ON CONFLICT ...
# RETURNING, now(), interval arithmetic) that SQLite cannot run, so these are
# real integration tests against a disposable database, not mocks. Point
# DPR_TEST_DATABASE_URL at a throwaway Postgres and the dpr_pg* fixtures work;
# otherwise every test that depends on them is skipped (not failed), so the
# rest of the suite stays runnable without Postgres installed.
#
#   docker run -d --rm -e POSTGRES_PASSWORD=test -e POSTGRES_DB=dpr_test \
#       -p 55432:5432 postgres:16-alpine
#   export DPR_TEST_DATABASE_URL=postgresql+psycopg://postgres:test@localhost:55432/dpr_test

DPR_TEST_DSN = os.environ.get(
    "DPR_TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:test@localhost:55432/dpr_test",
)


@pytest.fixture(scope="session")
def dpr_pg_engine():
    """Uses the same apply_migrations() the real app runs at startup, so the
    schema under test is exactly the schema production gets."""
    engine = engine_for(DPR_TEST_DSN)
    try:
        apply_migrations(engine)
    except Exception as exc:  # noqa: BLE001 - any connection/driver failure
        engine.dispose()
        pytest.skip(f"no test Postgres reachable at {DPR_TEST_DSN!r}: {exc}")
    yield engine
    engine.dispose()


@pytest.fixture
def dpr_pg(dpr_pg_engine):
    """A clean set of DPR tables for one test."""
    with dpr_pg_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE dpr_upload_staging, dpr_report_artifacts, "
                "dpr_analysis_branch_status, dpr_analysis_runs"
            )
        )
    return dpr_pg_engine
