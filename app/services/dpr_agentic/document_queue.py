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
from app.services.documents import StoredFile
from app.services.enrolment import DocumentNotAllowedError, EnrolmentService


def _project_context(svc, values: dict[str, str]) -> dict[str, str]:
    """The service's details as {key: value} (e.g. bank_name), skipping
    unset ones ("NA" or empty, as everywhere else)."""
    out = {}
    for detail in getattr(svc, "details", None) or []:
        value = (values.get(detail.field_id) or "").strip()
        if value and value.casefold() != "na":
            out[detail.key] = value
    return out


def queue_analysis(
    engine,
    settings: Settings,
    *,
    person_id: str,
    code: str,
    file: StoredFile,
    project_context: dict[str, str],
    wallet_status: str,
    wallet_error: str | None = None,
    wallet_reference: str | None = None,
) -> tuple[str, str | None]:
    """Queue the DPR analysis of a document already in hand; returns
    (analysis_status, run_id). Shared by the upload endpoints above and the
    email intake (app/services/intake/processor.py). NOT_QUEUED without the
    DPR database or staging key; RATE_LIMITED past the per-customer daily cap;
    ALREADY_ANALYSED when this exact document was already analysed (its run
    is COMPLETED; re-uploading a FAILED one queues it again).
    Never runs analysis inline: scripts/dpr_worker.py picks it up."""
    key = settings.dpr_staging_encryption_key
    if engine is None or key is None:
        return "NOT_QUEUED", None
    from app.services.dpr_agentic.branch_status import (
        init_branches,
        mark_wallet_retry,
        mark_wallet_stored,
    )
    from app.services.dpr_agentic.jobs import get_run, requeue_failed, submit_capped
    from app.services.dpr_agentic.upload_staging import stage_upload

    # Per-customer cap, across all their services -- never blocks the
    # Wallet write, only whether analysis gets queued. Counted and inserted
    # atomically (jobs.submit_capped).
    source_ref = f"wallet-person-custom-field:{person_id}:{code}"
    submitted = submit_capped(
        engine,
        cap_prefix=f"wallet-person-custom-field:{person_id}:%",
        since=datetime.now(timezone.utc) - timedelta(hours=24),
        limit=settings.dpr_agentic_daily_submission_limit,
        service_request_id=f"{person_id}:{code}",
        document_sha256=hashlib.sha256(file.data).hexdigest(),
        source_reference=source_ref,
    )
    if submitted is None:
        return "RATE_LIMITED", None
    run_id, created = submitted
    if not created:
        # The same document again: its run already exists (idempotency key).
        existing = get_run(engine, run_id) or {}
        state = existing.get("status")
        if state == "COMPLETED":
            # Already analysed: nothing to stage, the report is there.
            return "ALREADY_ANALYSED", run_id
        if state == "RUNNING":
            # In progress, and it still has its staged bytes.
            return "QUEUED", run_id
        if state == "FAILED" and not requeue_failed(engine, run_id):
            return "QUEUED", run_id  # requeued concurrently by another upload
        # FAILED (now requeued) or QUEUED: (re)stage below. A QUEUED run
        # with no staged bytes (stage_upload failed last time) is never
        # listed for a worker until they are there.
    init_branches(engine, run_id)
    stage_upload(
        engine,
        run_id,
        file,
        key.get_secret_value().encode(),
        project_context=project_context,
    )
    if wallet_status == "STORED":
        mark_wallet_stored(engine, run_id, wallet_reference or source_ref)
    else:
        mark_wallet_retry(
            engine,
            run_id,
            wallet_error or "UNKNOWN",
            datetime.now(timezone.utc) + timedelta(minutes=5),
        )
    return "QUEUED", run_id


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

    # A Wallet failure must never block analysis of the bytes we already
    # have in hand.
    try:
        await service.upload_document(person_id, code, file, broker_id, ctx)
        wallet_status, wallet_error, wallet_reference = "STORED", None, source_ref
    except Exception as exc:  # noqa: BLE001 - recorded, never blocks analysis
        wallet_status = "FAILED"
        wallet_error = type(exc).__name__
        wallet_reference = None

    analysis_status, run_id = queue_analysis(
        request.app.state.dpr_engine,
        settings,
        person_id=person_id,
        code=code,
        file=file,
        project_context=_project_context(svc, values),
        wallet_status=wallet_status,
        wallet_error=wallet_error,
        wallet_reference=wallet_reference,
    )

    return {
        "wallet_storage_status": wallet_status,
        "wallet_error": wallet_error,
        "analysis_status": analysis_status,
        "report_status": "NOT_PUBLISHED",
        "run_id": run_id,
    }
