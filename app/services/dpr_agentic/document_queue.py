"""Shared "store the document and queue its analysis" logic.

Used by both the customer's self-service upload
(PUT /customer/services/{code}/document-with-analysis) and the BD's upload on
a customer's behalf (PUT /bd/customers/{id}/services/{code}/document-with-
analysis) -- same gate, same staging, same rate limit. Never runs analysis
inline; scripts/dpr_worker.py picks it up later.

Raises UnknownServiceError / CustomerNotFoundError / DocumentNotAllowedError
from app.services.enrolment, exactly like the plain (non-analysis) upload
endpoints -- the caller catches them the same way it already does for every
other route in app/api/v1/customer.py and app/api/v1/bd.py.
"""

import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, status

from app.api.uploads import read_upload
from app.clients.vendor_client import VendorCallContext
from app.core.config import Settings
from app.services.enrolment import DocumentNotAllowedError, EnrolmentService


async def upload_and_queue_analysis(
    request: Request,
    filename: str,
    person_id: str,
    code: str,
    broker_id: str | None,
    service: EnrolmentService,
    settings: Settings,
    ctx: VendorCallContext,
) -> dict:
    if not settings.dpr_agentic_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if not settings.dpr_agentic_gemini_api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "DPR model is not configured"
        )

    # Validate eligibility before spending any effort on an unauthorized or
    # unpaid service. Existing API is the authority.
    svc = service._service(code)  # noqa: SLF001 - UnknownServiceError
    values = await service._values(person_id, broker_id, ctx)  # noqa: SLF001
    view = service._view(values, svc)  # noqa: SLF001
    if not view.can_upload:
        raise DocumentNotAllowedError("Document upload is not currently authorized")

    file = await read_upload(request, filename, settings)
    source_ref = f"wallet-person-custom-field:{person_id}:{code}"
    service_request_id = f"{person_id}:{code}"

    # A Wallet failure must never block analysis of the bytes we already
    # have in hand.
    try:
        await service.upload_document(person_id, code, file, broker_id, ctx)
        wallet_status, wallet_error, wallet_reference = "STORED", None, source_ref
    except Exception as exc:  # noqa: BLE001 - recorded, never blocks analysis
        wallet_status = "FAILED"
        wallet_error = type(exc).__name__
        wallet_reference = None

    engine = request.app.state.dpr_engine
    key = settings.dpr_staging_encryption_key
    run_id: str | None = None
    analysis_status = "NOT_QUEUED"
    if engine is not None and key is not None:
        from app.services.dpr_agentic.branch_status import (
            init_branches,
            mark_wallet_retry,
            mark_wallet_stored,
        )
        from app.services.dpr_agentic.jobs import count_recent_submissions, submit
        from app.services.dpr_agentic.upload_staging import stage_upload

        # Per-customer cap, across all their services -- never blocks the
        # Wallet write above, only whether analysis gets queued.
        recent = count_recent_submissions(
            engine,
            f"wallet-person-custom-field:{person_id}:%",
            datetime.now(timezone.utc) - timedelta(hours=24),
        )
        if recent >= settings.dpr_agentic_daily_submission_limit:
            analysis_status = "RATE_LIMITED"
        else:
            run_id, _ = submit(
                engine,
                service_request_id=service_request_id,
                document_sha256=hashlib.sha256(file.data).hexdigest(),
                source_reference=source_ref,
            )
            init_branches(engine, run_id)
            stage_upload(engine, run_id, file, key.get_secret_value().encode())
            if wallet_status == "STORED":
                mark_wallet_stored(engine, run_id, wallet_reference)
            else:
                mark_wallet_retry(
                    engine,
                    run_id,
                    wallet_error or "UNKNOWN",
                    datetime.now(timezone.utc) + timedelta(minutes=5),
                )
            analysis_status = "QUEUED"

    return {
        "wallet_storage_status": wallet_status,
        "wallet_error": wallet_error,
        "analysis_status": analysis_status,
        "report_status": "NOT_PUBLISHED",
        "run_id": run_id,
    }
