"""OTP verification of a customer's mobile number and email.

Send, then verify; the verify response's `verification_token` goes with the
save that needs it (registering a customer, changing an email). See
app/services/otp.py.
"""

from fastapi import APIRouter, Depends, Response

from app.api.deps import (
    get_authenticated_context,
    get_otp_service,
    get_token_role,
    get_token_user_id,
)
from app.clients.vendor_client import VendorCallContext
from app.core.roles import Role
from app.schemas.otp import (
    OtpSettings,
    SendOtpRequest,
    SendOtpResponse,
    VerifyOtpRequest,
    VerifyOtpResponse,
)
from app.services.otp import OtpService

router = APIRouter(prefix="/otp", tags=["otp"])


@router.get("/settings", response_model=OtpSettings)
async def otp_settings(service: OtpService = Depends(get_otp_service)) -> OtpSettings:
    """Whether OTPs are required, and the limits the dashboard should show."""
    return service.settings()


@router.post("/send", response_model=SendOtpResponse)
async def send_otp(
    payload: SendOtpRequest,
    response: Response,
    user_id: str = Depends(get_token_user_id),
    role: Role = Depends(get_token_role),
    service: OtpService = Depends(get_otp_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> SendOtpResponse:
    """Send an OTP to a mobile (SMS) or email.

    `register` is BD-only and refuses (409) a mobile already registered;
    `update` needs the customer to be the BD's own, or the caller themselves.
    Never retried: a retry would send a second OTP.
    """
    response.headers["Cache-Control"] = "no-store"
    return await service.send(payload, user_id, role, ctx)


@router.post("/verify", response_model=VerifyOtpResponse)
async def verify_otp(
    payload: VerifyOtpRequest,
    response: Response,
    user_id: str = Depends(get_token_user_id),
    service: OtpService = Depends(get_otp_service),
) -> VerifyOtpResponse:
    """Check the code. `otp_max_attempts` wrong codes end the session."""
    response.headers["Cache-Control"] = "no-store"
    return await service.verify(payload, user_id)
