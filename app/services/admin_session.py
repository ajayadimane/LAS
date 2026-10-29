"""The middleware's own super-admin session with the vendor.

Some vendor calls need more than the dashboard user's token: creating an IAM
user makes the vendor call Keycloak's admin API with the caller's token, and a
BD token gets 403 there (verified live 2026-10-27). For those calls only, we
sign in as the super admin ourselves and use that token instead.

The admin token never leaves this process: it is not returned, not logged
(the client redacts `accessToken`), and not stored outside memory. One
session per app, created in the lifespan, shared by every request.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import TypeVar

from app.clients.vendor_client import VendorCallContext, VendorClient
from app.core.config import Settings
from app.core.exceptions import VendorError
from app.core.logging import get_logger
from app.core.token_claims import int_claim, read_claims

logger = get_logger(__name__)

SUPERADMIN_LOGIN_PATH = "/IdentityAccessMgmt/user/superadmin"

# Sign in again this long before the token's own expiry.
_EXPIRY_MARGIN_SECONDS = 60

T = TypeVar("T")


def _admin_failure(code: str, message: str) -> VendorError:
    """Our failure to act as admin, never the dashboard user's fault.

    Raised as a 502 so the dashboard never reads it as its own session
    expiring (a 401 would sign the BD out) or its own bad credentials (400).
    """
    return VendorError(
        502, error_code=code, error_message=message, path=SUPERADMIN_LOGIN_PATH
    )


class AdminSession:
    def __init__(self, client: VendorClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def _token_for(self, ctx: VendorCallContext, *, fresh: bool) -> str:
        async with self._lock:
            if not fresh and self._token and time.time() < self._expires_at:
                return self._token
            self._token = None
            token, expires_at = await self._sign_in(ctx)
            # A token without `exp` is used for this call only.
            if expires_at:
                self._token = token
                self._expires_at = expires_at - _EXPIRY_MARGIN_SECONDS
            return token

    async def _sign_in(self, ctx: VendorCallContext) -> tuple[str, int | None]:
        s = self._settings
        if not s.vendor_admin_user_id or not s.vendor_admin_password:
            logger.error("admin_not_configured: set VENDOR_ADMIN_USER_ID/PASSWORD")
            raise _admin_failure(
                "ADMIN_NOT_CONFIGURED", "The vendor admin account is not configured"
            )
        payload = {
            "userId": s.vendor_admin_user_id,
            "password": s.vendor_admin_password.get_secret_value(),
        }
        # Unlike the user login, this call must carry a signed-in user's
        # bearer: without one the gateway answers 401 with an empty body
        # before IAM sees it (verified live 2026-10-27). So it goes out with
        # the dashboard user's own token and headers.
        try:
            body = await self._client.post(SUPERADMIN_LOGIN_PATH, payload, ctx)
        except VendorError as exc:
            if exc.http_status >= 500:
                raise
            logger.error(
                "admin_login_failed vendor_status=%s code=%r txn=%s",
                exc.http_status,
                exc.error_code,
                exc.transaction_id,
            )
            raise _admin_failure(
                "ADMIN_LOGIN_FAILED", "The vendor rejected the admin account"
            ) from exc
        token = body.get("accessToken") if isinstance(body, dict) else None
        if not token:
            raise _admin_failure(
                "ADMIN_LOGIN_FAILED", "Admin login returned no access token"
            )
        return token, int_claim(read_claims(token), "exp")

    async def run(
        self,
        ctx: VendorCallContext,
        call: Callable[[VendorCallContext], Awaitable[T]],
    ) -> T:
        """Run `call` with the admin token in place of the user's.

        On a 401 the cached token is dropped and the call made once more with
        a fresh one. That repeats a write, but only one the vendor refused at
        authentication, so it was never carried out.
        """
        token = await self._token_for(ctx, fresh=False)
        try:
            return await call(replace(ctx, token=token))
        except VendorError as exc:
            if exc.http_status != 401:
                raise
        logger.info("admin_token_rejected: signing in again")
        token = await self._token_for(ctx, fresh=True)
        try:
            return await call(replace(ctx, token=token))
        except VendorError as exc:
            if exc.http_status == 401:
                raise _admin_failure(
                    "ADMIN_TOKEN_REJECTED", "The vendor rejected a fresh admin token"
                ) from exc
            raise
