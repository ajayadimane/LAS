"""Low-level HTTP wrapper around the iWallet vendor API.

Every vendor call in this service goes through here. Responsibilities:

* attach the four headers the vendor makes mandatory on every endpoint
* normalise the programMngrId / programManagerId spelling split, so the rest
  of the app only ever sees `programManagerId`
* parse the 8-field error envelope into a VendorError
* retry read-only /walletInfoRetrieval calls, and nothing else
* log method, path, status, duration and the vendor's correlation ids,
  without ever logging a token or a password
"""

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import Settings
from app.core.exceptions import (
    VendorError,
    VendorTimeoutError,
    VendorUnavailableError,
)
from app.core.logging import get_logger, redact

logger = get_logger(__name__)

# The one spelling the rest of the app uses.
CANONICAL_PM_KEY = "programManagerId"
# The vendor's alternative spelling, used by exactly three endpoints.
VENDOR_SHORT_PM_KEY = "programMngrId"

# Verified against api_docs/: login, forgot-password and open-wallet take
# `programMngrId`; the other ten endpoints take `programManagerId`.
SHORT_PM_PATHS = frozenset(
    {
        "/IdentityAccessMgmt/user/login",
        "/IdentityAccessMgmt/user/password/forgot",
        "/regularWallet/",
    }
)

# Read-only service prefix. These are POSTs, but they are side-effect free,
# so they are the only paths we retry. See CLAUDE.md > Rules.
RETRYABLE_PREFIX = "/walletInfoRetrieval"


@dataclass(frozen=True)
class VendorCallContext:
    """Per-request values the vendor demands on every call.

    `token` is the vendor JWT belonging to the dashboard user, passed straight
    through. This service holds no token of its own.
    """

    client_ip: str
    latitude: str
    longitude: str
    token: str | None = None


class _TransientVendorError(Exception):
    """Internal marker so tenacity knows what is worth another attempt."""

    def __init__(self, cause: VendorError) -> None:
        self.cause = cause
        super().__init__(str(cause))


def to_vendor_payload(path: str, payload: Mapping[str, Any]) -> dict:
    """Rename the canonical program-manager key to whatever this path wants."""
    if path in SHORT_PM_PATHS and CANONICAL_PM_KEY in payload:
        return {
            (VENDOR_SHORT_PM_KEY if k == CANONICAL_PM_KEY else k): v
            for k, v in payload.items()
        }
    return dict(payload)


def from_vendor_payload(data: Any) -> Any:
    """Rename the vendor's short spelling back to the canonical one."""
    if isinstance(data, dict):
        return {
            (CANONICAL_PM_KEY if k == VENDOR_SHORT_PM_KEY else k): from_vendor_payload(
                v
            )
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [from_vendor_payload(item) for item in data]
    return data


class VendorClient:
    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        self._http = http
        self._settings = settings

    # -- headers ---------------------------------------------------------

    def build_headers(self, ctx: VendorCallContext) -> dict:
        """The four mandatory headers, plus auth when we have a token."""
        headers = {
            "Content-Type": "application/json",
            "X-Original-Forwarded-For": ctx.client_ip,
            "X-Geolocation-Latitude": ctx.latitude,
            "X-Geolocation-Longitude": ctx.longitude,
        }
        if ctx.token:
            headers["Authorization"] = f"Bearer {ctx.token}"
        return headers

    # -- public verbs ----------------------------------------------------

    async def post(
        self, path: str, payload: Mapping[str, Any], ctx: VendorCallContext
    ) -> Any:
        return await self.request("POST", path, payload, ctx)

    async def put(
        self, path: str, payload: Mapping[str, Any], ctx: VendorCallContext
    ) -> Any:
        return await self.request("PUT", path, payload, ctx)

    async def request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any],
        ctx: VendorCallContext,
    ) -> Any:
        attempts = (
            self._settings.vendor_retry_attempts if self.is_retryable(path) else 1
        )
        retrying = AsyncRetrying(
            stop=stop_after_attempt(attempts),
            wait=wait_exponential(
                multiplier=self._settings.vendor_retry_backoff_seconds
            ),
            retry=retry_if_exception_type(_TransientVendorError),
            reraise=True,
        )
        try:
            return await retrying(self._send_once, method, path, payload, ctx)
        except _TransientVendorError as exc:
            raise exc.cause from None

    @staticmethod
    def is_retryable(path: str) -> bool:
        return path.startswith(RETRYABLE_PREFIX)

    # -- one attempt -----------------------------------------------------

    async def _send_once(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any],
        ctx: VendorCallContext,
    ) -> Any:
        body = to_vendor_payload(path, payload)
        headers = self.build_headers(ctx)
        started = time.perf_counter()

        logger.debug(
            "vendor_request method=%s path=%s authed=%s body=%s",
            method,
            path,
            bool(ctx.token),
            redact(body),
        )

        try:
            response = await self._http.request(
                method, path, json=body, headers=headers
            )
        except httpx.TimeoutException as exc:
            self._log_call(method, path, "TIMEOUT", started)
            raise _TransientVendorError(
                VendorTimeoutError(
                    f"Vendor timed out after {self._settings.vendor_timeout_seconds}s",
                    method=method,
                    path=path,
                )
            ) from exc
        except httpx.TransportError as exc:
            self._log_call(method, path, "TRANSPORT_ERROR", started)
            raise _TransientVendorError(
                VendorUnavailableError(
                    "Vendor is unreachable", method=method, path=path
                )
            ) from exc

        parsed = self._decode(response)
        txn = parsed.get("transactionId") if isinstance(parsed, dict) else None
        corr = parsed.get("correlationId") if isinstance(parsed, dict) else None
        self._log_call(method, path, response.status_code, started, txn, corr)

        if response.is_success:
            return from_vendor_payload(parsed)

        if response.is_redirect:
            # Never followed: a redirected POST re-sends the body and token,
            # and a redirect means our path is wrong. The gateway redirects
            # e.g. /customermgmt/person -> /customermgmt/person/ (307); the
            # Location says which path to put in the code.
            logger.warning(
                "vendor_redirect method=%s path=%s status=%s location=%s "
                "-- fix the path constant",
                method,
                path,
                response.status_code,
                response.headers.get("location"),
            )

        error = VendorError.from_envelope(
            response.status_code, parsed, method=method, path=path
        )
        # Only server-side failures are worth repeating, and only on paths we
        # already decided are safe to repeat.
        if response.status_code >= 500 and self.is_retryable(path):
            raise _TransientVendorError(error)
        raise error

    @staticmethod
    def _decode(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None

    @staticmethod
    def _log_call(
        method: str,
        path: str,
        status: Any,
        started: float,
        txn: str | None = None,
        corr: str | None = None,
    ) -> None:
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        logger.info(
            "vendor_call method=%s path=%s status=%s duration_ms=%s txn=%s corr=%s",
            method,
            path,
            status,
            duration_ms,
            txn,
            corr,
        )
