"""Routes for a signed-in customer, acting on their own record.

The customer's person id is their login id (the mobile number), so every
route here works on the token's user and takes no person id: a customer can
only ever reach their own record.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.api.deps import (
    get_app_settings,
    get_authenticated_context,
    get_bd_service,
    get_enrolment_service,
    require_customer,
)
from app.api.uploads import file_response, read_upload
from app.clients.vendor_client import VendorCallContext
from app.core.config import Settings
from app.schemas.customer import CustomerProfile
from app.schemas.enrolment import (
    CustomerServices,
    DocumentUploadResponse,
    ServiceRequest,
    ServiceRequestResponse,
)
from app.services.bd import BdService, CustomerNotFoundError
from app.services.enrolment import (
    DocumentNotAllowedError,
    DocumentNotStoredError,
    EnrolmentService,
    PaymentNotAllowedError,
    PaymentRequestError,
    UnknownServiceError,
)

router = APIRouter(prefix="/customer", tags=["customer"])

_NO_RECORD = HTTPException(
    status.HTTP_404_NOT_FOUND, "Your customer record was not found."
)


def _unknown_service(code: str) -> HTTPException:
    return HTTPException(
        status.HTTP_404_NOT_FOUND, f"'{code}' is not a service you can take here."
    )


@router.get("/profile", response_model=CustomerProfile)
async def my_profile(
    me: str = Depends(require_customer),
    bd: BdService = Depends(get_bd_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> CustomerProfile:
    """My name, contact details and address."""
    try:
        return await bd.profile(me, ctx)
    except CustomerNotFoundError:
        raise _NO_RECORD from None


@router.get("/services", response_model=CustomerServices)
async def my_services(
    me: str = Depends(require_customer),
    service: EnrolmentService = Depends(get_enrolment_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> CustomerServices:
    """My services: opted, requested, paid, documents."""
    try:
        return await service.customer_services(me, None, ctx)
    except CustomerNotFoundError:
        raise _NO_RECORD from None


@router.post("/services/request", response_model=ServiceRequestResponse)
async def request_services(
    payload: ServiceRequest,
    me: str = Depends(require_customer),
    service: EnrolmentService = Depends(get_enrolment_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> ServiceRequestResponse:
    """Opt into services. Pay later (and, for now, pay now) waits for my BD."""
    try:
        return await service.request(me, payload, ctx)
    except CustomerNotFoundError:
        raise _NO_RECORD from None
    except UnknownServiceError as exc:
        raise _unknown_service(str(exc)) from None
    except PaymentRequestError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    except PaymentNotAllowedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.put("/services/{code}/document", response_model=DocumentUploadResponse)
async def upload_my_document(
    code: str,
    request: Request,
    filename: str = Query(min_length=1, max_length=255),
    me: str = Depends(require_customer),
    service: EnrolmentService = Depends(get_enrolment_service),
    settings: Settings = Depends(get_app_settings),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> DocumentUploadResponse:
    """Upload the document for one of my paid services. Body is the raw file."""
    file = await read_upload(request, filename, settings)
    try:
        return await service.upload_document(me, code, file, None, ctx)
    except CustomerNotFoundError:
        raise _NO_RECORD from None
    except UnknownServiceError:
        raise _unknown_service(code) from None
    except DocumentNotAllowedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.get("/services/{code}/document")
async def download_my_document(
    code: str,
    me: str = Depends(require_customer),
    service: EnrolmentService = Depends(get_enrolment_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> Response:
    try:
        return file_response(await service.document(me, code, None, ctx))
    except CustomerNotFoundError:
        raise _NO_RECORD from None
    except UnknownServiceError:
        raise _unknown_service(code) from None
    except DocumentNotStoredError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No document uploaded for this service."
        ) from None
