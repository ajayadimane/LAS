"""Low-level HTTP wrapper around the IBC OTP service (not iWallet).

Contract (user, 2026-11-04):

    POST /ibc/v1/otp/generate
      {"identifier": "<mobile|email>", "channel": "text"|"email"}
      -> {"success": true, "message": "OTP sent successfully.",
          "request": {...}, "response_code": "f4b1899c663d4cd2"}
    POST /ibc/v1/otp/verify     {"response_code": "...", "otp": "861283"}

Auth is an `api-key` header; no bearer, none of iWallet's four headers.

Nothing here is ever retried: a repeated generate sends a second OTP, and a
repeated verify spends an attempt. Neither the OTP, the key nor the
response_code is ever logged.
"""

import time
from typing import Any

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

GENERATE_PATH = "/ibc/v1/otp/generate"
VERIFY_PATH = "/ibc/v1/otp/verify"


class OtpServiceError(Exception):
    """The OTP service failed, could not be reached, or said no."""

    def __init__(
        self, message: str, *, status: int | None = None, body: Any = None
    ) -> None:
        # None = no HTTP response at all (timeout, connection refused).
        self.status = status
        self.body = body
        super().__init__(message)


class OtpServiceTimeout(OtpServiceError):
    pass


def build_otp_http_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.otp_base_url or "http://otp.invalid",
        timeout=httpx.Timeout(settings.otp_timeout_seconds),
        verify=settings.otp_verify_ssl,
    )


class OtpClient:
    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        self._http = http
        self._settings = settings

    @property
    def configured(self) -> bool:
        key = self._settings.otp_api_key
        return bool(self._settings.otp_base_url and key and key.get_secret_value())

    async def generate(self, identifier: str, channel: str) -> dict:
        """Sends an OTP. The body, which carries `response_code`."""
        return await self._post(
            GENERATE_PATH, {"identifier": identifier, "channel": channel}
        )

    async def verify(self, response_code: str, otp: str) -> dict:
        """Checks an OTP. Raises OtpServiceError with the status on a refusal."""
        return await self._post(
            VERIFY_PATH, {"response_code": response_code, "otp": otp}
        )

    async def _post(self, path: str, payload: dict) -> dict:
        key = self._settings.otp_api_key
        headers = {
            "Content-Type": "application/json",
            "api-key": key.get_secret_value() if key else "",
        }
        started = time.perf_counter()
        try:
            response = await self._http.post(path, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            self._log(path, "TIMEOUT", started)
            raise OtpServiceTimeout("The OTP service timed out") from exc
        except httpx.TransportError as exc:
            self._log(path, "TRANSPORT_ERROR", started)
            raise OtpServiceError("The OTP service is unreachable") from exc

        try:
            body = response.json()
        except ValueError:
            body = None
        self._log(path, response.status_code, started, body)
        if not response.is_success:
            raise OtpServiceError(
                f"The OTP service returned {response.status_code}",
                status=response.status_code,
                body=body,
            )
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _log(path: str, status: Any, started: float, body: Any = None) -> None:
        # `success` and `message` only: the body echoes the identifier and
        # carries the response_code, which together let anyone verify.
        success = body.get("success") if isinstance(body, dict) else None
        message = body.get("message") if isinstance(body, dict) else None
        logger.info(
            "otp_call method=POST path=%s status=%s duration_ms=%s success=%s "
            "message=%r",
            path,
            status,
            round((time.perf_counter() - started) * 1000, 1),
            success,
            message,
        )
