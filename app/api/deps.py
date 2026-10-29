"""FastAPI dependencies: settings, the shared client, and per-request context."""

from fastapi import Depends, HTTPException, Request, status

from app.clients.vendor_client import VendorCallContext, VendorClient
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.core.roles import Role, role_of
from app.core.token_claims import read_claims
from app.services.admin_session import AdminSession
from app.services.bd import BdService
from app.services.custom_field import CustomFieldService
from app.services.enrolment import EnrolmentService
from app.services.identity import IdentityService
from app.services.mailer import Mailer
from app.services.person import PersonService
from app.services.wallet import WalletService


def get_app_settings(request: Request) -> Settings:
    """Settings for this app instance, set by create_app."""
    return getattr(request.app.state, "settings", None) or get_settings()


def get_vendor_client(request: Request) -> VendorClient:
    client: VendorClient | None = getattr(request.app.state, "vendor_client", None)
    if client is None:  # pragma: no cover - only if lifespan did not run
        raise RuntimeError("Vendor client is not initialised")
    return client


def _client_ip(request: Request) -> str:
    """The end user's IP, for X-Original-Forwarded-For.

    Prefer what the dashboard forwards; fall back to the socket peer.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def get_bearer_token(request: Request) -> str | None:
    """The vendor JWT the dashboard is passing through, if it sent one."""
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def get_call_context(
    request: Request,
    settings: Settings = Depends(get_app_settings),
) -> VendorCallContext:
    """Context for endpoints that do not need a token (login)."""
    return VendorCallContext(
        client_ip=_client_ip(request),
        latitude=request.headers.get(
            "x-geolocation-latitude", settings.default_geo_latitude
        ),
        longitude=request.headers.get(
            "x-geolocation-longitude", settings.default_geo_longitude
        ),
        token=get_bearer_token(request),
    )


def get_authenticated_context(
    ctx: VendorCallContext = Depends(get_call_context),
) -> VendorCallContext:
    """Context for endpoints the vendor requires a Bearer token on.

    Rejected here rather than spending a round trip to learn the same thing.
    """
    if not ctx.token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token. Call /api/v1/identity/login first.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return ctx


def get_token_user_id(
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> str:
    """The userId the dashboard's token was issued to (`preferred_username`).

    The claim is read unverified, which is safe only because the same token is
    forwarded to the vendor, which rejects a forged or expired one. So use this
    only to scope a call that carries that token.
    """
    user_id = (read_claims(ctx.token or "") or {}).get("preferred_username")
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The token does not say which user it belongs to. Sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_id


def get_admin_session(request: Request) -> AdminSession:
    session: AdminSession | None = getattr(request.app.state, "admin_session", None)
    if session is None:  # pragma: no cover - only if lifespan did not run
        raise RuntimeError("Admin session is not initialised")
    return session


def get_token_role(
    ctx: VendorCallContext = Depends(get_authenticated_context),
    settings: Settings = Depends(get_app_settings),
) -> Role:
    """ "bd" or "customer", from the token's groupId (see app/core/roles.py)."""
    role = role_of(ctx.token or "", settings)
    if role is None:
        raise AppError(
            403,
            "NOT_A_DASHBOARD_USER",
            "This account is not set up as a BD or a customer. Please contact support.",
        )
    return role


def require_bd(
    user_id: str = Depends(get_token_user_id),
    role: Role = Depends(get_token_role),
) -> str:
    """The signed-in BD's id. A customer gets a 403 that says why."""
    if role != "bd":
        raise AppError(
            403,
            "BD_ONLY",
            "This is only available to BDs. You are signed in as a customer.",
        )
    return user_id


def require_customer(
    user_id: str = Depends(get_token_user_id),
    role: Role = Depends(get_token_role),
) -> str:
    """The signed-in customer's id (their person id). A BD gets a 403."""
    if role != "customer":
        raise AppError(
            403,
            "CUSTOMER_ONLY",
            "This is only available to customers. You are signed in as a BD.",
        )
    return user_id


def get_mailer(request: Request) -> Mailer | None:
    return getattr(request.app.state, "mailer", None)


def get_bd_service(
    client: VendorClient = Depends(get_vendor_client),
    settings: Settings = Depends(get_app_settings),
    admin: AdminSession = Depends(get_admin_session),
    mailer: Mailer | None = Depends(get_mailer),
) -> BdService:
    return BdService(client, settings, admin, mailer)


def get_wallet_service(
    client: VendorClient = Depends(get_vendor_client),
    settings: Settings = Depends(get_app_settings),
) -> WalletService:
    return WalletService(client, settings)


def get_identity_service(
    client: VendorClient = Depends(get_vendor_client),
    settings: Settings = Depends(get_app_settings),
) -> IdentityService:
    return IdentityService(client, settings)


def get_person_service(
    client: VendorClient = Depends(get_vendor_client),
    settings: Settings = Depends(get_app_settings),
) -> PersonService:
    return PersonService(client, settings)


def get_custom_field_service(
    client: VendorClient = Depends(get_vendor_client),
    settings: Settings = Depends(get_app_settings),
) -> CustomFieldService:
    return CustomFieldService(client, settings)


def get_enrolment_service(
    client: VendorClient = Depends(get_vendor_client),
    settings: Settings = Depends(get_app_settings),
    bd: BdService = Depends(get_bd_service),
) -> EnrolmentService:
    return EnrolmentService(client, settings, bd)
