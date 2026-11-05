"""OTP verification of a customer's mobile number and email (user, 2026-11-04).

Flow, with no database:

1. `send`: checks the caller may verify this value for this customer, asks
   the OTP service to send a code, and returns an `otp_session` -- a token we
   sign, binding the service's `response_code` to the channel, value,
   purpose, customer and caller. The response_code never leaves it unsigned,
   so the dashboard cannot swap in another number.
2. `verify`: checks the code with the OTP service and returns a
   `verification_token`, signed proof that this value was verified.
3. The save (`POST /bd/customers`, `PUT /bd/customers/{id}/email`) calls
   `require_proof`, which accepts only a proof for exactly that value,
   purpose, customer and caller.

Limits we enforce (the service's own are undocumented): `otp_max_attempts`
wrong codes per OTP sent, `otp_resend_cooldown_seconds` between sends to the
same value, and the session / proof lifetimes. Attempts and cooldowns are
kept in memory (`OtpGuard`), which is correct for the single worker the
Dockerfile runs; with several workers each would count separately.

Why `send` makes a vendor call first: the dashboard token's claims are read
unverified, which is safe only when the token is also forwarded to iWallet.
The OTP service never sees it, so without that call anyone could forge a
token and use us to send SMS. The call is also useful: on `register` it
catches an already-registered mobile before an OTP is spent on it.
"""

import re
import secrets
import time
from dataclasses import dataclass, field

from app.clients.otp_client import OtpClient, OtpServiceError, OtpServiceTimeout
from app.clients.vendor_client import VendorCallContext
from app.core.config import Settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.core.roles import Role
from app.core.signed_tokens import sign, unsign
from app.schemas.otp import (
    OtpSettings,
    SendOtpRequest,
    SendOtpResponse,
    VerifyOtpRequest,
    VerifyOtpResponse,
)
from app.services.bd import BdService, CustomerNotFoundError

logger = get_logger(__name__)

SESSION_TYP = "otp_session"
PROOF_TYP = "otp_proof"
# Our channel names -> the OTP service's.
SERVICE_CHANNEL = {"mobile": "text", "email": "email"}

_MOBILE = re.compile(r"^\d{10}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class OtpGuard:
    """Per-process OTP state: the signing key, attempts and cooldowns."""

    secret: bytes
    # response_code -> (wrong attempts, forget after)
    _attempts: dict[str, tuple[int, float]] = field(default_factory=dict)
    # response_codes already verified once: a code proves one thing, once.
    _used: dict[str, float] = field(default_factory=dict)
    # "<channel>:<value>" -> time of the last send
    _last_sent: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, settings: Settings) -> "OtpGuard":
        configured = settings.otp_token_secret
        if configured and configured.get_secret_value():
            return cls(secret=configured.get_secret_value().encode())
        logger.warning(
            "otp_token_secret_unset -- using a random key; OTP sessions and "
            "proofs will not survive a restart. Set OTP_TOKEN_SECRET."
        )
        return cls(secret=secrets.token_bytes(32))

    def _prune(self, now: float) -> None:
        for store in (self._attempts, self._used):
            for key in [k for k, v in store.items() if _until(v) < now]:
                del store[key]

    def seconds_until_resend(self, key: str, cooldown: int) -> int:
        last = self._last_sent.get(key)
        return 0 if last is None else max(0, int(last + cooldown - time.time() + 0.999))

    def mark_sent(self, key: str) -> None:
        now = time.time()
        self._last_sent[key] = now
        # Keep the map from growing without bound.
        if len(self._last_sent) > 10_000:
            self._last_sent = {
                k: t for k, t in self._last_sent.items() if now - t < 3600
            }

    def attempts(self, code: str) -> int:
        self._prune(time.time())
        return self._attempts.get(code, (0, 0.0))[0]

    def fail(self, code: str, forget_after: float) -> int:
        count = self.attempts(code) + 1
        self._attempts[code] = (count, forget_after)
        return count

    def is_used(self, code: str) -> bool:
        self._prune(time.time())
        return code in self._used

    def use(self, code: str, forget_after: float) -> None:
        self._used[code] = forget_after
        self._attempts.pop(code, None)


def _until(value: float | tuple[int, float]) -> float:
    return value[1] if isinstance(value, tuple) else value


def normalise(channel: str, value: str) -> str:
    """The canonical form compared everywhere: 10 digits, or a lowercased email."""
    value = value.strip()
    if channel == "mobile":
        digits = re.sub(r"[\s-]", "", value)
        if not _MOBILE.fullmatch(digits):
            raise AppError(422, "OTP_INVALID_MOBILE", "Enter a 10-digit mobile number.")
        return digits
    lowered = value.lower()
    if not _EMAIL.fullmatch(lowered) or len(lowered) > 100:
        raise AppError(422, "OTP_INVALID_EMAIL", "Enter a valid email address.")
    return lowered


def mask(channel: str, value: str) -> str:
    if channel == "mobile":
        return "*" * (len(value) - 4) + value[-4:]
    local, _, domain = value.partition("@")
    return f"{local[:2]}{'*' * max(1, len(local) - 2)}@{domain}"


class OtpService:
    def __init__(
        self,
        client: OtpClient,
        guard: OtpGuard,
        settings: Settings,
        bd: BdService,
    ) -> None:
        self._client = client
        self._guard = guard
        self._settings = settings
        self._bd = bd

    def settings(self) -> OtpSettings:
        s = self._settings
        return OtpSettings(
            required=s.otp_required,
            code_length=s.otp_length,
            max_attempts=s.otp_max_attempts,
            resend_after_seconds=s.otp_resend_cooldown_seconds,
        )

    # -- send ----------------------------------------------------------------

    async def send(
        self,
        req: SendOtpRequest,
        user_id: str,
        role: Role,
        ctx: VendorCallContext,
    ) -> SendOtpResponse:
        if not self._client.configured:
            raise AppError(
                503, "OTP_NOT_CONFIGURED", "OTP verification is not set up yet."
            )
        value = normalise(req.channel, req.value)
        person_id = req.person_id.strip()
        await self._check_access(req, value, person_id, user_id, role, ctx)

        s = self._settings
        cooldown_key = f"{req.channel}:{value}"
        wait = self._guard.seconds_until_resend(
            cooldown_key, s.otp_resend_cooldown_seconds
        )
        if wait:
            raise AppError(
                429,
                "OTP_RESEND_TOO_SOON",
                f"An OTP was just sent. You can send another in {wait} seconds.",
            )

        try:
            body = await self._client.generate(value, SERVICE_CHANNEL[req.channel])
        except OtpServiceError as exc:
            raise _service_failure(exc, "OTP_SEND_FAILED") from None
        code = body.get("response_code")
        if body.get("success") is not True or not isinstance(code, str) or not code:
            logger.warning(
                "otp_send_refused channel=%s success=%r message=%r",
                req.channel,
                body.get("success"),
                body.get("message"),
            )
            raise AppError(
                502, "OTP_SEND_FAILED", "The OTP could not be sent. Please try again."
            )
        self._guard.mark_sent(cooldown_key)

        token = sign(
            {
                "rc": code,
                "ch": req.channel,
                "val": value,
                "pur": req.purpose,
                "pid": person_id,
                "sub": user_id.casefold(),
            },
            self._guard.secret,
            SESSION_TYP,
            s.otp_session_ttl_seconds,
        )
        logger.info(
            "otp_sent channel=%s purpose=%s person_id=%s by=%s",
            req.channel,
            req.purpose,
            person_id,
            user_id,
        )
        return SendOtpResponse(
            otp_session=token,
            sent_to=mask(req.channel, value),
            expires_at=int(time.time()) + s.otp_session_ttl_seconds,
            resend_after_seconds=s.otp_resend_cooldown_seconds,
            max_attempts=s.otp_max_attempts,
            code_length=s.otp_length,
        )

    async def _check_access(
        self,
        req: SendOtpRequest,
        value: str,
        person_id: str,
        user_id: str,
        role: Role,
        ctx: VendorCallContext,
    ) -> None:
        """Who may verify what. Each branch also makes one iWallet call with
        the caller's token, which is what proves the token is real."""
        if req.purpose == "register":
            if role != "bd":
                raise AppError(403, "BD_ONLY", "Only a BD can register a customer.")
            if not _MOBILE.fullmatch(person_id):
                raise AppError(
                    422,
                    "OTP_INVALID_MOBILE",
                    "The customer being registered needs a 10-digit mobile number.",
                )
            if req.channel == "mobile" and value != person_id:
                raise AppError(
                    422,
                    "OTP_VALUE_MISMATCH",
                    "The mobile number to verify is the one being registered.",
                )
            try:
                await self._bd.person(person_id, ctx)
            except CustomerNotFoundError:
                return
            raise AppError(
                409,
                "CUSTOMER_ALREADY_REGISTERED",
                "A customer with this mobile number is already registered.",
            )

        # update
        try:
            if role == "bd":
                await self._bd.owned_person(person_id, user_id, ctx)
            else:
                if person_id.casefold() != user_id.casefold():
                    raise CustomerNotFoundError(person_id)
                await self._bd.person(person_id, ctx)
        except CustomerNotFoundError:
            raise AppError(404, "CUSTOMER_NOT_FOUND", "Customer not found.") from None

    # -- verify --------------------------------------------------------------

    async def verify(self, req: VerifyOtpRequest, user_id: str) -> VerifyOtpResponse:
        s = self._settings
        claims = unsign(req.otp_session, self._guard.secret, SESSION_TYP)
        if claims is None or claims.get("sub") != user_id.casefold():
            raise AppError(
                400,
                "OTP_SESSION_EXPIRED",
                "This OTP has expired. Send a new one.",
            )
        code: str = claims["rc"]
        forget_after = float(claims["exp"])
        if self._guard.is_used(code):
            raise AppError(
                400, "OTP_ALREADY_USED", "This OTP was already used. Send a new one."
            )
        if self._guard.attempts(code) >= s.otp_max_attempts:
            raise _too_many()

        try:
            body = await self._client.verify(code, req.otp)
            correct = body.get("success") is True or body.get("verified") is True
        except OtpServiceError as exc:
            # A refusal of the code itself is a 4xx; anything else is the
            # service failing, which must not cost the user an attempt.
            if exc.status is None or exc.status >= 500 or exc.status in (401, 403):
                raise _service_failure(exc, "OTP_VERIFY_FAILED") from None
            correct = False

        if not correct:
            failures = self._guard.fail(code, forget_after)
            left = s.otp_max_attempts - failures
            logger.info(
                "otp_wrong channel=%s person_id=%s attempts_left=%s",
                claims["ch"],
                claims["pid"],
                left,
            )
            if left <= 0:
                raise _too_many()
            raise AppError(
                400,
                "OTP_INVALID",
                f"Incorrect OTP. You have {left} "
                f"attempt{'s' if left != 1 else ''} left.",
            )

        self._guard.use(code, forget_after)
        proof = sign(
            {k: claims[k] for k in ("ch", "val", "pur", "pid", "sub")},
            self._guard.secret,
            PROOF_TYP,
            s.otp_proof_ttl_seconds,
        )
        logger.info(
            "otp_verified channel=%s purpose=%s person_id=%s by=%s",
            claims["ch"],
            claims["pur"],
            claims["pid"],
            user_id,
        )
        return VerifyOtpResponse(
            channel=claims["ch"],
            value=claims["val"],
            verification_token=proof,
            expires_at=int(time.time()) + s.otp_proof_ttl_seconds,
        )

    # -- the save ------------------------------------------------------------

    def require_proof(
        self,
        token: str | None,
        *,
        channel: str,
        value: str,
        purpose: str,
        person_id: str,
        user_id: str,
    ) -> None:
        """Refuses the save unless `token` proves exactly this verification.
        A no-op when OTP_REQUIRED is off."""
        if not self._settings.otp_required:
            return
        label = "mobile number" if channel == "mobile" else "email address"
        claims = unsign(token, self._guard.secret, PROOF_TYP)
        expected = {
            "ch": channel,
            "val": normalise(channel, value),
            "pur": purpose,
            "pid": person_id.strip(),
            "sub": user_id.casefold(),
        }
        if claims is None or any(claims.get(k) != v for k, v in expected.items()):
            raise AppError(
                400,
                "OTP_VERIFICATION_REQUIRED",
                f"Verify the {label} with an OTP first.",
            )


def _too_many() -> AppError:
    return AppError(
        400,
        "OTP_ATTEMPTS_EXCEEDED",
        "Incorrect OTP. You have no attempts left; please request a new OTP.",
    )


def _service_failure(exc: OtpServiceError, code: str) -> AppError:
    logger.warning(
        "otp_service_failure code=%s status=%s error=%s", code, exc.status, exc
    )
    if isinstance(exc, OtpServiceTimeout):
        return AppError(504, code, "The OTP service did not respond. Please try again.")
    return AppError(502, code, "The OTP service is not available. Please try again.")
