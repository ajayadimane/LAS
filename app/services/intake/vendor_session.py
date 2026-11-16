"""The intake worker's own vendor session.

A dashboard request carries its user's token; a background job has none, yet
every vendor call needs one -- even the super-admin sign-in that login
creation uses (app/services/admin_session.py). So the worker signs in as a
configured BD account (settings.intake_vendor_user_id/password) and reuses
that token until shortly before it expires. The token stays in memory, is
never logged, and serves only intake work.
"""

import asyncio
import time

from app.clients.vendor_client import VendorCallContext, VendorClient
from app.core.config import Settings
from app.core.logging import get_logger
from app.schemas.identity import LoginRequest
from app.services.identity import IdentityService

logger = get_logger(__name__)

# Sign in again this long before the token's own expiry.
_EXPIRY_MARGIN_SECONDS = 120
# A token without `exp`: reuse it for at most this long.
_FALLBACK_LIFETIME_SECONDS = 15 * 60


class IntakeNotConfiguredError(RuntimeError):
    """No intake vendor account in settings."""


class IntakeVendorSession:
    def __init__(self, client: VendorClient, settings: Settings) -> None:
        self._settings = settings
        self._identity = IdentityService(client, settings)
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    def _bare_context(self, token: str | None = None) -> VendorCallContext:
        s = self._settings
        return VendorCallContext(
            client_ip=s.intake_client_ip,
            latitude=s.default_geo_latitude,
            longitude=s.default_geo_longitude,
            token=token,
        )

    async def context(self) -> VendorCallContext:
        """A call context carrying a valid token, signing in if needed."""
        s = self._settings
        if not (s.intake_vendor_user_id and s.intake_vendor_password):
            raise IntakeNotConfiguredError(
                "INTAKE_VENDOR_USER_ID / INTAKE_VENDOR_PASSWORD are not set"
            )
        async with self._lock:
            if self._token is None or time.time() >= self._expires_at:
                login = await self._identity.login(
                    LoginRequest(
                        user_id=s.intake_vendor_user_id,
                        password=s.intake_vendor_password,
                    ),
                    self._bare_context(),
                )
                self._token = login.access_token
                self._expires_at = (
                    login.expires_at - _EXPIRY_MARGIN_SECONDS
                    if login.expires_at
                    else time.time() + _FALLBACK_LIFETIME_SECONDS
                )
                logger.info("intake_vendor_signed_in user_id=%s", login.user_id)
            return self._bare_context(self._token)

    def invalidate(self) -> None:
        """Forget the token (after a 401); the next call signs in again."""
        self._token = None
        self._expires_at = 0.0
