"""Routes for a signed-in customer, acting on their own record.

The customer's person id is their login id (the mobile number), so every
route here works on the token's user and takes no person id: a customer can
only ever reach their own record.
"""

from typing import Literal

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
from app.services.dpr_agentic.document_queue import upload_and_queue_analysis
from app.services.dpr_agentic.report_http import (
    resolve_annexures,
    resolve_findings,
    resolve_report,
    resolve_report_pdf,
    resolve_summary,
    resolve_versions,
)
from app.services.dpr_agentic.schemas import AnalysisResult, Finding
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


@router.put(
    "/services/{code}/document-with-analysis",
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_and_analyze_document(
    code: str,
    request: Request,
    filename: str = Query(min_length=1, max_length=255),
    me: str = Depends(require_customer),
    service: EnrolmentService = Depends(get_enrolment_service),
    settings: Settings = Depends(get_app_settings),
    ctx: VendorCallContext = Depends(get_authenticated_context),
):
    """Store the document and queue its analysis; never runs it inline.

    Feature-gated; existing upload endpoint remains unchanged. The Wallet
    write happens synchronously here (same as endpoint 23); analysis itself
    is picked up later by scripts/dpr_worker.py -- poll
    GET /services/{code}/report for status and, once available, the report.
    """
    try:
        return await upload_and_queue_analysis(
            request, filename, me, code, None, service, settings, ctx
        )
    except CustomerNotFoundError:
        raise _NO_RECORD from None
    except UnknownServiceError:
        raise _unknown_service(code) from None
    except DocumentNotAllowedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


def _dpr_engine(request: Request, settings: Settings):
    if not settings.dpr_agentic_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    engine = request.app.state.dpr_engine
    if engine is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "DPR report storage is not configured"
        )
    return engine


@router.get("/services/{code}/report", response_model=AnalysisResult)
async def my_report(
    code: str,
    request: Request,
    response: Response,
    format: Literal["json", "pdf"] = Query(default="json"),
    version: int | None = Query(default=None, ge=1),
    me: str = Depends(require_customer),
    service: EnrolmentService = Depends(get_enrolment_service),
    settings: Settings = Depends(get_app_settings),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> AnalysisResult | Response:
    """The DPR analysis report for one of my services, once it may be
    released -- the latest version by default, or a specific `version`.

    Always re-checks payment against the vendor at call time; see
    app/services/dpr_agentic/report_access.py for the full gate.
    `?format=pdf` serves the pre-rendered six-page CAM report stored
    alongside that version; it is never rendered on this request.
    """
    engine = _dpr_engine(request, settings)
    if format == "pdf":
        return await resolve_report_pdf(
            engine=engine,
            person_id=me,
            code=code,
            broker_id=None,
            service=service,
            ctx=ctx,
            version=version,
            not_found=_NO_RECORD,
            unknown_service=_unknown_service,
        )
    return await resolve_report(
        engine=engine,
        person_id=me,
        code=code,
        broker_id=None,
        service=service,
        ctx=ctx,
        response=response,
        version=version,
        not_found=_NO_RECORD,
        unknown_service=_unknown_service,
    )


@router.get("/services/{code}/report/findings", response_model=list[Finding])
async def my_report_findings(
    code: str,
    request: Request,
    response: Response,
    version: int | None = Query(default=None, ge=1),
    me: str = Depends(require_customer),
    service: EnrolmentService = Depends(get_enrolment_service),
    settings: Settings = Depends(get_app_settings),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> list[Finding]:
    """Just the findings from the same gated report as `.../report`."""
    engine = _dpr_engine(request, settings)
    return await resolve_findings(
        engine=engine,
        person_id=me,
        code=code,
        broker_id=None,
        service=service,
        ctx=ctx,
        response=response,
        version=version,
        not_found=_NO_RECORD,
        unknown_service=_unknown_service,
    )


@router.get("/services/{code}/report/annexures")
async def my_report_annexures(
    code: str,
    request: Request,
    response: Response,
    version: int | None = Query(default=None, ge=1),
    me: str = Depends(require_customer),
    service: EnrolmentService = Depends(get_enrolment_service),
    settings: Settings = Depends(get_app_settings),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> dict:
    """Annexure B: project cost, means of finance, financial ratios and
    per-scenario break-even calculations, each with its full trace, for the
    same gated report as `.../report`."""
    engine = _dpr_engine(request, settings)
    return await resolve_annexures(
        engine=engine,
        person_id=me,
        code=code,
        broker_id=None,
        service=service,
        ctx=ctx,
        response=response,
        version=version,
        not_found=_NO_RECORD,
        unknown_service=_unknown_service,
    )


@router.get("/services/{code}/report/summary")
async def my_report_summary(
    code: str,
    request: Request,
    response: Response,
    version: int | None = Query(default=None, ge=1),
    me: str = Depends(require_customer),
    service: EnrolmentService = Depends(get_enrolment_service),
    settings: Settings = Depends(get_app_settings),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> dict:
    """Dashboard status card (Section 17): readiness status, Must/Should/
    Good finding counts, a short financial overview and an outstanding-
    items count -- for the same gated report as `.../report`."""
    engine = _dpr_engine(request, settings)
    return await resolve_summary(
        engine=engine,
        person_id=me,
        code=code,
        broker_id=None,
        service=service,
        ctx=ctx,
        response=response,
        version=version,
        not_found=_NO_RECORD,
        unknown_service=_unknown_service,
    )


@router.get("/services/{code}/report/versions", response_model=list[int])
async def my_report_versions(
    code: str,
    request: Request,
    response: Response,
    me: str = Depends(require_customer),
    service: EnrolmentService = Depends(get_enrolment_service),
    settings: Settings = Depends(get_app_settings),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> list[int]:
    """Every version number ever stored for this service, oldest first."""
    engine = _dpr_engine(request, settings)
    return await resolve_versions(
        engine=engine,
        person_id=me,
        code=code,
        broker_id=None,
        service=service,
        ctx=ctx,
        response=response,
        not_found=_NO_RECORD,
        unknown_service=_unknown_service,
    )
