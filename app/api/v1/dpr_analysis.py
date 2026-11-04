"""Internal, disabled-by-default, request-scoped Agentic DPR preview API.

Mounted under the same public /api/v1 prefix as every customer/BD route
(app/api/v1/router.py) -- there is no separate internal network or port, so
the shared-secret token below (never the feature flag alone) is the only
thing standing between this and the public internet. A disabled feature
flag still 404s before the token is even checked, so toggling the flag off
does not even reveal the route exists."""

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.api.deps import get_app_settings
from app.core.config import Settings
from app.core.logging import get_logger
from app.services.dpr_agentic.gemini import GeminiAgentClient, GeminiAgentError
from app.services.dpr_agentic.orchestrator import analyze
from app.services.dpr_agentic.schemas import AnalysisInput, AnalysisResult

logger = get_logger(__name__)

router = APIRouter(prefix="/internal/dpr-agentic", tags=["internal-dpr-agentic"])


def _check_internal_token(
    request: Request, x_internal_token: str | None, settings: Settings
) -> None:
    configured = settings.dpr_agentic_internal_token
    if (
        not configured
        or not x_internal_token
        or not secrets.compare_digest(x_internal_token, configured.get_secret_value())
    ):
        # Never log the token itself (sent or configured) -- just that an
        # attempt was rejected, for operational visibility on a route with
        # no network-level isolation.
        logger.warning(
            "dpr_internal_token_rejected path=%s client_ip=%s",
            request.url.path,
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/analyze", response_model=AnalysisResult)
async def run_analysis(
    payload: AnalysisInput,
    request: Request,
    x_internal_token: str | None = Header(default=None),
    settings: Settings = Depends(get_app_settings),
):
    if not settings.dpr_agentic_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    _check_internal_token(request, x_internal_token, settings)
    if not settings.dpr_agentic_gemini_api_key:
        raise HTTPException(status_code=503, detail="DPR model is not configured")
    client = GeminiAgentClient(
        request.app.state.gemini_http_client,
        settings.dpr_agentic_gemini_api_key.get_secret_value(),
        settings.dpr_agentic_gemini_model,
    )
    try:
        return await analyze(
            payload, client, cam_engine_enabled=settings.cam_engine_enabled
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GeminiAgentError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/inspect-upload")
async def inspect_uploaded_document(
    request: Request,
    filename: str,
    wallet_document_id: str,
    service_request_id: str,
    x_internal_token: str | None = Header(default=None),
    settings: Settings = Depends(get_app_settings),
):
    """Internal post-Wallet-upload adapter. Caller MUST provide authoritative Wallet ID.

    Inspection does not persist bytes. Non-DPR/missing visual extraction is
    NOT sent to agents. This endpoint cannot itself prove upload ownership;
    integration must enforce it.
    """
    from app.services.dpr_agentic.document_pipeline.pipeline import (
        DocumentProcessingError,
        inspect_document,
    )
    from app.services.dpr_agentic.schemas import SourcePage

    if not settings.dpr_agentic_enabled:
        raise HTTPException(404, "Not found")
    _check_internal_token(request, x_internal_token, settings)
    if not wallet_document_id.strip() or not service_request_id.strip():
        raise HTTPException(
            422, "Authoritative Wallet and Core LAS references required"
        )
    raw = await request.body()
    try:
        parsed = inspect_document(raw, filename)
    except DocumentProcessingError as exc:
        raise HTTPException(422, str(exc)) from exc
    info = {
        "mime": parsed.mime,
        "document_class": parsed.doc_class,
        "classification_confidence": parsed.confidence,
        "classification_method": parsed.classification_method,
        "document_sha256": parsed.sha256,
        "page_count": len(parsed.pages),
        "requires_vision": parsed.needs_vision,
    }
    if parsed.needs_vision:
        return {"status": "needs_vision_fallback", "inspection": info, "analysis": None}
    if parsed.doc_class != "DPR" or parsed.confidence < 0.8:
        return {
            "status": "classification_review_required",
            "inspection": info,
            "analysis": None,
        }
    if not settings.dpr_agentic_gemini_api_key:
        return {"status": "ready_for_analysis", "inspection": info, "analysis": None}
    client = GeminiAgentClient(
        request.app.state.gemini_http_client,
        settings.dpr_agentic_gemini_api_key.get_secret_value(),
        settings.dpr_agentic_gemini_model,
    )
    payload = AnalysisInput(
        service_request_id=service_request_id,
        wallet_document_id=wallet_document_id,
        document_sha256=parsed.sha256,
        pages=[SourcePage(**p) for p in parsed.pages if p["text"].strip()],
    )
    try:
        result = await analyze(
            payload, client, cam_engine_enabled=settings.cam_engine_enabled
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except GeminiAgentError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {
        "status": "analyzed",
        "inspection": info,
        "analysis": result.model_dump(mode="json"),
    }
