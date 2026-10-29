"""Vendor error envelope, and its mapping to the errors we show the dashboard.

The vendor returns the same 8-field envelope on every documented failure:

    timestamp, transactionId, correlationId, definedErrorCode,
    status, errorCode, errorMessage, debugMessage

`errorCode` and `definedErrorCode` carry English prose ("The program manager
not found"), not a stable machine code, so the mapping below keys on the HTTP
status and never on those strings. See CLAUDE.md > Third-party API details.
"""

import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.error_messages import friendly_message, is_known_code, upstream_code
from app.core.logging import get_logger

logger = get_logger(__name__)

# Vendor HTTP status -> the status we return to the dashboard.
# Keyed on the HTTP status only. Do not add errorCode-based branches here.
_STATUS_MAP = {
    400: 400,
    401: 401,  # forwarded vendor JWT rejected; dashboard should re-login
    403: 403,
    404: 404,
    409: 409,
    422: 422,
    429: 429,  # undocumented by the vendor, mapped defensively
}

# Anything the vendor fails at that is not a client-correctable 4xx is a
# gateway problem from the dashboard's point of view, not our 500.
_DEFAULT_STATUS = 502


class VendorError(Exception):
    """A failure reported by the vendor, carrying the parsed envelope."""

    def __init__(
        self,
        http_status: int,
        *,
        error_message: str | None = None,
        error_code: str | None = None,
        defined_error_code: str | None = None,
        debug_message: str | None = None,
        transaction_id: str | None = None,
        correlation_id: str | None = None,
        timestamp: str | None = None,
        envelope_status: int | None = None,
        method: str | None = None,
        path: str | None = None,
    ) -> None:
        self.http_status = http_status
        self.error_message = error_message or "Vendor request failed"
        self.error_code = error_code
        self.defined_error_code = defined_error_code
        # debug_message is for our logs only; it is never returned downstream.
        self.debug_message = debug_message
        self.transaction_id = transaction_id
        self.correlation_id = correlation_id
        self.timestamp = timestamp
        # The envelope's own "status" field. The docs' samples always show 404
        # regardless of the real HTTP status, so this is recorded for support
        # but is never used for mapping.
        self.envelope_status = envelope_status
        self.method = method
        self.path = path
        super().__init__(self.error_message)

    @classmethod
    def from_envelope(
        cls,
        http_status: int,
        body: Any,
        *,
        method: str | None = None,
        path: str | None = None,
    ) -> "VendorError":
        if not isinstance(body, dict):
            return cls(http_status, method=method, path=path)
        return cls(
            http_status,
            error_message=body.get("errorMessage"),
            error_code=body.get("errorCode"),
            defined_error_code=body.get("definedErrorCode"),
            debug_message=body.get("debugMessage"),
            transaction_id=body.get("transactionId"),
            correlation_id=body.get("correlationId"),
            timestamp=body.get("timestamp"),
            envelope_status=body.get("status"),
            method=method,
            path=path,
        )

    @property
    def dashboard_status(self) -> int:
        return _STATUS_MAP.get(self.http_status, _DEFAULT_STATUS)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"VendorError(http_status={self.http_status}, "
            f"message={self.error_message!r}, txn={self.transaction_id!r})"
        )


class VendorUnavailableError(VendorError):
    """The vendor could not be reached at all (DNS, TLS, connection refused)."""

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(503, error_message=message, **kwargs)

    @property
    def dashboard_status(self) -> int:
        return 502


class VendorTimeoutError(VendorError):
    """The vendor did not respond inside the configured timeout."""

    def __init__(
        self, message: str = "Vendor request timed out", **kwargs: Any
    ) -> None:
        super().__init__(504, error_message=message, **kwargs)

    @property
    def dashboard_status(self) -> int:
        return 504


def error_details(exc: VendorError) -> dict:
    """What the dashboard sees of a vendor error.

    `message` is ours, from app.core.error_messages -- never the vendor's
    `errorMessage`, which is a machine code, and never `debugMessage`.
    `code` is the vendor's raw code so the dashboard can branch on it; it is
    null when the vendor sent no envelope (a 429 or an empty-bodied 500), or
    when it is not a SCREAMING_SNAKE code: the IAM service has been seen
    putting raw upstream errors there, internal URLs included (2026-10-27).
    Ids the vendor sends as the string "null" are null too.
    """
    code = dashboard_code(exc.error_code)
    return {
        "status": exc.dashboard_status,
        "code": code,
        "message": friendly_message(code, exc.dashboard_status),
        "transaction_id": _real_id(exc.transaction_id),
        "correlation_id": _real_id(exc.correlation_id),
    }


_MACHINE_CODE = re.compile(r"[A-Z][A-Z0-9_]*")


def dashboard_code(raw: str | None) -> str | None:
    """The vendor's code if it is a SCREAMING_SNAKE machine code, our code for
    a recognised raw upstream error, else None (raw text can carry internals)."""
    if raw and _MACHINE_CODE.fullmatch(raw):
        return raw
    return upstream_code(raw)


def _real_id(value: str | None) -> str | None:
    return None if value in (None, "", "null") else value


def _error_body(exc: VendorError) -> dict:
    return {"error": error_details(exc)}


async def vendor_error_handler(_request: Request, exc: VendorError) -> JSONResponse:
    logger.warning(
        "vendor_error method=%s path=%s vendor_status=%s envelope_status=%s "
        "dashboard_status=%s txn=%s corr=%s code=%r vendor_message=%r debug=%r",
        exc.method,
        exc.path,
        exc.http_status,
        exc.envelope_status,
        exc.dashboard_status,
        exc.transaction_id,
        exc.correlation_id,
        exc.error_code,
        exc.error_message,
        exc.debug_message,
    )
    if exc.error_code and not is_known_code(dashboard_code(exc.error_code)):
        # The dashboard fell back to a generic message. Surfacing this is how
        # new vendor codes get discovered and added to error_messages.py.
        logger.warning(
            "unmapped_vendor_error_code code=%r path=%s vendor_status=%s "
            "-- add it to app/core/error_messages.py",
            exc.error_code,
            exc.path,
            exc.http_status,
        )
    return JSONResponse(status_code=exc.dashboard_status, content=_error_body(exc))


class AppError(Exception):
    """A refusal of ours (not the vendor's), sent in the same `{"error": ...}`
    shape as vendor errors so the dashboard handles both alike."""

    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    logger.info("app_error status=%s code=%s", exc.status, exc.code)
    return JSONResponse(
        status_code=exc.status,
        content={
            "error": {
                "status": exc.status,
                "code": exc.code,
                "message": exc.message,
                "transaction_id": None,
                "correlation_id": None,
            }
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(VendorError, vendor_error_handler)
    app.add_exception_handler(AppError, app_error_handler)
