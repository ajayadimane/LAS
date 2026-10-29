"""Identity routes. Thin: validate, call the service, return a schema."""

from fastapi import APIRouter, Depends, status

from app.api.deps import (
    get_authenticated_context,
    get_call_context,
    get_identity_service,
    require_bd,
)
from app.clients.vendor_client import VendorCallContext
from app.schemas.identity import (
    CreateUserRequest,
    CreateUserResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    LoginResponse,
)
from app.services.identity import IdentityService

router = APIRouter(prefix="/identity", tags=["identity"])


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    service: IdentityService = Depends(get_identity_service),
    ctx: VendorCallContext = Depends(get_call_context),
) -> LoginResponse:
    """Authenticate against the vendor and hand the JWT back to the dashboard.

    Pass-through auth: we keep no session. The dashboard sends the returned
    token as its own Authorization header on later calls and we forward it.
    """
    return await service.login(payload, ctx)


@router.post(
    "/users", response_model=CreateUserResponse, status_code=status.HTTP_201_CREATED
)
async def create_user(
    payload: CreateUserRequest,
    _bd: str = Depends(require_bd),
    service: IdentityService = Depends(get_identity_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> CreateUserResponse:
    return await service.create_user(payload, ctx)


@router.post(
    "/password/forgot",
    response_model=ForgotPasswordResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def forgot_password(
    payload: ForgotPasswordRequest,
    service: IdentityService = Depends(get_identity_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> ForgotPasswordResponse:
    """Trigger the vendor's password-reset email.

    Note: the vendor requires a Bearer token here, so a locked-out user cannot
    self-serve. Treated as an admin-initiated reset until the vendor confirms
    otherwise (api_docs/ENDPOINTS.md > Open questions).
    """
    return await service.forgot_password(payload, ctx)
