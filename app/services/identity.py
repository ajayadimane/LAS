"""Identity business logic: translate our schemas to the vendor's and back."""

import time

from app.clients.vendor_client import VendorCallContext, VendorClient
from app.core.config import Settings
from app.core.exceptions import AppError, VendorError
from app.core.logging import get_logger
from app.core.roles import group_ids, role_of
from app.core.token_claims import int_claim, read_claims
from app.schemas.common import VendorAck
from app.schemas.identity import (
    CreateUserRequest,
    CreateUserResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    LoginResponse,
)

logger = get_logger(__name__)

LOGIN_PATH = "/IdentityAccessMgmt/user/login"
ADD_USER_PATH = "/IdentityAccessMgmt/user/"
FORGOT_PASSWORD_PATH = "/IdentityAccessMgmt/user/password/forgot"


class IdentityService:
    def __init__(self, client: VendorClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    def _program_manager_id(self, supplied: str | None) -> str:
        return supplied or self._settings.vendor_program_manager_id

    async def login(self, req: LoginRequest, ctx: VendorCallContext) -> LoginResponse:
        # Always uses the canonical key; the client renames it for this path.
        payload = {
            "userId": req.user_id,
            "programManagerId": self._program_manager_id(req.program_manager_id),
            "password": req.password.get_secret_value(),
        }
        body = await self._client.post(LOGIN_PATH, payload, ctx)

        token = body.get("accessToken") if isinstance(body, dict) else None
        if not token:
            # 200 with no token is a vendor contract violation, not a user error.
            raise VendorError(
                502,
                error_message="Vendor login succeeded but returned no access token",
                method="POST",
                path=LOGIN_PATH,
            )

        role = role_of(token, self._settings)
        if role is None:
            # A valid vendor account, but in neither the BD nor the customer
            # group: it has no place in this dashboard. Its token is dropped.
            logger.warning(
                "login_without_dashboard_role user_id=%s groups=%s",
                req.user_id,
                group_ids(token),
            )
            raise AppError(
                403,
                "NOT_A_DASHBOARD_USER",
                "This account is not set up as a BD or a customer, so it cannot "
                "sign in here. Please contact support.",
            )
        expires_at = int_claim(read_claims(token), "exp")
        return LoginResponse(
            access_token=token,
            user_id=req.user_id,
            role=role,
            expires_at=expires_at,
            expires_in=max(expires_at - int(time.time()), 0) if expires_at else None,
            transaction_id=body.get("transactionId"),
            correlation_id=body.get("correlationId"),
        )

    async def create_user(
        self, req: CreateUserRequest, ctx: VendorCallContext
    ) -> CreateUserResponse:
        payload = {
            "userName": req.user_name,
            "email": str(req.email),
            "firstName": req.first_name,
            "lastName": req.last_name,
            "programManagerId": self._program_manager_id(req.program_manager_id),
            "groupName": req.group_name,
            "mobileCountryCode": req.mobile_country_code,
            "mobileNumber": req.mobile_number,
            "twoFactorAuthCodeType": req.two_factor_code_type,
            "twoFactorAuthCodeDeliveryChannel": req.two_factor_delivery_channel,
            "password": req.password.get_secret_value(),
        }
        body = await self._client.post(ADD_USER_PATH, payload, ctx)
        return CreateUserResponse(**VendorAck.from_vendor(body).model_dump())

    async def forgot_password(
        self, req: ForgotPasswordRequest, ctx: VendorCallContext
    ) -> ForgotPasswordResponse:
        payload = {
            "userId": req.user_id,
            "programManagerId": self._program_manager_id(req.program_manager_id),
            "email": str(req.email),
        }
        body = await self._client.post(FORGOT_PASSWORD_PATH, payload, ctx)
        return ForgotPasswordResponse(**VendorAck.from_vendor(body).model_dump())
