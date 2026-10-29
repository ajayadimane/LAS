"""BD dashboard routes."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import (
    get_app_settings,
    get_authenticated_context,
    get_bd_service,
    get_enrolment_service,
    require_bd,
)
from app.api.uploads import file_response, read_upload
from app.clients.vendor_client import VendorCallContext
from app.core.config import Settings
from app.core.exceptions import error_details
from app.schemas.bd import (
    BdCustomer,
    BdCustomerList,
    CreateCustomerRequest,
    CreateCustomerResponse,
    CreateLoginResponse,
    OpenWalletResponse,
    UpdateAddressRequest,
    UpdateCustomerResponse,
    UpdateEmailRequest,
)
from app.schemas.enrolment import (
    AuthorizeRequest,
    CustomerServices,
    DocumentUploadResponse,
    PaymentRequest,
    PaymentResponse,
)
from app.services.bd import BdService, CustomerIncompleteError, CustomerNotFoundError
from app.services.enrolment import (
    DocumentNotAllowedError,
    DocumentNotStoredError,
    EnrolmentService,
    PaymentNotAllowedError,
    PaymentOutcomeUnknownError,
    PaymentRequestError,
    UnknownServiceError,
)

router = APIRouter(prefix="/bd", tags=["bd"])


def get_bd_id(
    broker_id: str | None = Query(
        default=None,
        description="The BD id saved at login. Defaults to the token's user; "
        "if sent, it must be that user.",
    ),
    token_user: str = Depends(require_bd),
) -> str:
    """The signed-in BD's id, which is the brokerId of their customers.

    The vendor does not check that a brokerId belongs to the token, so we do.
    Keycloak lowercases usernames, hence the case-insensitive comparison; the
    id is passed on as the dashboard saved it.
    """
    if broker_id is not None and broker_id.casefold() != token_user.casefold():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only see your own customers.",
        )
    return broker_id or token_user


@router.get("/customers", response_model=BdCustomerList)
async def list_customers(
    bd_id: str = Depends(get_bd_id),
    service: BdService = Depends(get_bd_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> BdCustomerList:
    """The customers registered under the signed-in BD."""
    return await service.customers(bd_id, ctx)


@router.get("/customers/{person_id}", response_model=BdCustomer)
async def get_customer(
    person_id: str,
    bd_id: str = Depends(get_bd_id),
    service: BdService = Depends(get_bd_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> BdCustomer:
    """One of the signed-in BD's customers.

    404 both when the person does not exist and when they belong to another
    broker, so the response does not reveal which.
    """
    try:
        return await service.customer(person_id, bd_id, ctx)
    except CustomerNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found."
        ) from None


@router.post(
    "/customers",
    response_model=CreateCustomerResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_customer(
    payload: CreateCustomerRequest,
    response: Response,
    bd_id: str = Depends(get_bd_id),
    service: BdService = Depends(get_bd_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> CreateCustomerResponse:
    """Register a customer under the signed-in BD, open their wallet and
    create their login.

    201 once the customer is registered, even if a later step failed: check
    `wallet_opened` and `login_created`, and retry with
    `POST /customers/{person_id}/wallet` or `/login`. A registration failure
    (e.g. 409, already registered) is an error.
    """
    # The body carries the customer's temporary password.
    response.headers["Cache-Control"] = "no-store"
    return await service.create_customer(payload, bd_id, ctx)


@router.post("/customers/{person_id}/wallet", response_model=OpenWalletResponse)
async def open_wallet(
    person_id: str,
    bd_id: str = Depends(get_bd_id),
    service: BdService = Depends(get_bd_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> OpenWalletResponse:
    """Open (or confirm) the wallet of one of the signed-in BD's customers."""
    try:
        return await service.open_wallet(person_id, bd_id, ctx)
    except CustomerNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found."
        ) from None


@router.post("/customers/{person_id}/login", response_model=CreateLoginResponse)
async def create_login(
    person_id: str,
    response: Response,
    bd_id: str = Depends(get_bd_id),
    service: BdService = Depends(get_bd_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> CreateLoginResponse:
    """Create (or confirm) the login of one of the signed-in BD's customers.

    The generated password is in the response, once.
    """
    # The body carries a password: no proxy or browser may keep it.
    response.headers["Cache-Control"] = "no-store"
    try:
        return await service.create_login(person_id, bd_id, ctx)
    except CustomerNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found."
        ) from None
    except CustomerIncompleteError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None


# -- editing ----------------------------------------------------------------
# One route per vendor update, so each change succeeds or fails on its own.


@router.put("/customers/{person_id}/email", response_model=UpdateCustomerResponse)
async def update_email(
    person_id: str,
    payload: UpdateEmailRequest,
    bd_id: str = Depends(get_bd_id),
    service: BdService = Depends(get_bd_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> UpdateCustomerResponse:
    """Change a customer's email (person record only, not their login)."""
    try:
        return await service.update_email(person_id, payload, bd_id, ctx)
    except CustomerNotFoundError:
        raise _NOT_FOUND from None


@router.put("/customers/{person_id}/address", response_model=UpdateCustomerResponse)
async def update_address(
    person_id: str,
    payload: UpdateAddressRequest,
    bd_id: str = Depends(get_bd_id),
    service: BdService = Depends(get_bd_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> UpdateCustomerResponse:
    """Replace a customer's postal address."""
    try:
        return await service.update_address(person_id, payload, bd_id, ctx)
    except CustomerNotFoundError:
        raise _NOT_FOUND from None


# -- services, payment, documents ---------------------------------------------

_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found.")


def _unknown_service(code: str) -> HTTPException:
    return HTTPException(
        status.HTTP_404_NOT_FOUND,
        f"'{code}' is not a service that can be paid for or documented here.",
    )


@router.get("/customers/{person_id}/services", response_model=CustomerServices)
async def get_customer_services(
    person_id: str,
    bd_id: str = Depends(get_bd_id),
    service: EnrolmentService = Depends(get_enrolment_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> CustomerServices:
    """The customer's opted services, payment and document state."""
    try:
        return await service.customer_services(person_id, bd_id, ctx)
    except CustomerNotFoundError:
        raise _NOT_FOUND from None


@router.post(
    "/customers/{person_id}/payment",
    response_model=PaymentResponse,
    responses={502: {"description": "PAYMENT_OUTCOME_UNKNOWN: do not retry."}},
)
async def pay(
    person_id: str,
    payload: PaymentRequest,
    bd_id: str = Depends(get_bd_id),
    service: EnrolmentService = Depends(get_enrolment_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> PaymentResponse | JSONResponse:
    """Opt the customer into services and record or make the payment.

    `pay_now`: call once the BD has confirmed the QR payment was received.
    `pay_later`: needs both consents; moves the amount from the pool to the
    main account. 409 if the customer has already paid or a payment is in
    progress. A 502 with code PAYMENT_OUTCOME_UNKNOWN means the transfer may
    have happened: never retry it, contact support with the reference.
    """
    try:
        return await service.pay(person_id, payload, bd_id, ctx)
    except CustomerNotFoundError:
        raise _NOT_FOUND from None
    except UnknownServiceError as exc:
        raise _unknown_service(str(exc)) from None
    except PaymentRequestError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    except PaymentNotAllowedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except PaymentOutcomeUnknownError as exc:
        return outcome_unknown_response(exc)


def outcome_unknown_response(exc: PaymentOutcomeUnknownError) -> JSONResponse:
    """502 PAYMENT_OUTCOME_UNKNOWN: the transfer may have happened."""
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={
            "error": {
                **error_details(exc.cause),
                "status": status.HTTP_502_BAD_GATEWAY,
                "code": "PAYMENT_OUTCOME_UNKNOWN",
                "message": "We could not confirm whether the payment went "
                "through. Do not try again: contact support and quote the "
                "reference below.",
            }
        },
    )


@router.post(
    "/customers/{person_id}/services/{code}/authorize",
    response_model=PaymentResponse,
    responses={502: {"description": "PAYMENT_OUTCOME_UNKNOWN: do not retry."}},
)
async def authorize_request(
    person_id: str,
    code: str,
    payload: AuthorizeRequest,
    bd_id: str = Depends(get_bd_id),
    service: EnrolmentService = Depends(get_enrolment_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> PaymentResponse | JSONResponse:
    """Authorize a service the customer requested themselves.

    Pay later: `bd_consent` (standing guarantor) is required, and the pool
    transfer runs now. Pay now: confirms the customer's QR payment arrived.
    """
    try:
        return await service.authorize(person_id, code, payload.bd_consent, bd_id, ctx)
    except CustomerNotFoundError:
        raise _NOT_FOUND from None
    except UnknownServiceError:
        raise _unknown_service(code) from None
    except PaymentRequestError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    except PaymentNotAllowedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except PaymentOutcomeUnknownError as exc:
        return outcome_unknown_response(exc)


@router.post(
    "/customers/{person_id}/services/{code}/decline",
    response_model=CustomerServices,
)
async def decline_request(
    person_id: str,
    code: str,
    bd_id: str = Depends(get_bd_id),
    service: EnrolmentService = Depends(get_enrolment_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> CustomerServices:
    """Decline a service the customer requested. Nothing is charged."""
    try:
        return await service.decline(person_id, code, bd_id, ctx)
    except CustomerNotFoundError:
        raise _NOT_FOUND from None
    except UnknownServiceError:
        raise _unknown_service(code) from None
    except PaymentNotAllowedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.put(
    "/customers/{person_id}/services/{code}/document",
    response_model=DocumentUploadResponse,
)
async def upload_document(
    person_id: str,
    code: str,
    request: Request,
    filename: str = Query(min_length=1, max_length=255),
    bd_id: str = Depends(get_bd_id),
    service: EnrolmentService = Depends(get_enrolment_service),
    settings: Settings = Depends(get_app_settings),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> DocumentUploadResponse:
    """Upload the document for one opted, paid service. Body is the raw file;
    it is stored base64-encoded in the service's custom field."""
    file = await read_upload(request, filename, settings)
    try:
        return await service.upload_document(person_id, code, file, bd_id, ctx)
    except CustomerNotFoundError:
        raise _NOT_FOUND from None
    except UnknownServiceError:
        raise _unknown_service(code) from None
    except DocumentNotAllowedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.get("/customers/{person_id}/services/{code}/document")
async def download_document(
    person_id: str,
    code: str,
    bd_id: str = Depends(get_bd_id),
    service: EnrolmentService = Depends(get_enrolment_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> Response:
    try:
        return file_response(await service.document(person_id, code, bd_id, ctx))
    except CustomerNotFoundError:
        raise _NOT_FOUND from None
    except UnknownServiceError:
        raise _unknown_service(code) from None
    except DocumentNotStoredError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No document uploaded for this service."
        ) from None
