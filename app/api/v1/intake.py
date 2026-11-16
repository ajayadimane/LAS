"""Receives email-intake events from LAS_Email (service to service).

Mounted off app.main, outside the dashboard's /api/v1 surface, like the DPR
preview API. Disabled = 404 before the key is even checked. The shared key
is the only gate inside this app, so in production the reverse proxy should
also restrict this path to LAS_Email's address.

The route only validates and persists: it answers 202 as soon as the event
is stored, and the work happens later (app/services/intake/). LAS_Email
retries until it gets an answer, so a repeat of the same Message-ID is a
409 carrying the original id, which LAS_Email treats as delivered.
"""

import asyncio
import secrets

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.deps import get_app_settings
from app.core.config import Settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.schemas.intake import IntakeEvent, IntakeEventAccepted
from app.services.intake.events import IntakeEventInvalid, decode_attachments
from app.services.intake.store import insert_event

logger = get_logger(__name__)

router = APIRouter(prefix="/internal/v1/intake", tags=["internal-intake"])


def _check_key(request: Request, sent: str | None, settings: Settings) -> None:
    candidates = [
        s.get_secret_value()
        for s in (settings.intake_api_key, settings.intake_api_key_previous)
        if s is not None and s.get_secret_value()
    ]
    if sent and any(secrets.compare_digest(sent, c) for c in candidates):
        return
    # Never log the key, sent or configured.
    logger.warning(
        "intake_key_rejected client_ip=%s",
        request.client.host if request.client else "unknown",
    )
    raise AppError(status.HTTP_403_FORBIDDEN, "FORBIDDEN", "Forbidden")


def _validation_problems(exc: ValidationError) -> str:
    # Field locations and messages only: pydantic's `input` would echo the
    # email's content (customer PII) back into the response and the logs.
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
        for err in exc.errors()[:20]
    )


@router.post(
    "/events",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IntakeEventAccepted,
    responses={409: {"model": IntakeEventAccepted}},
)
async def receive_event(
    request: Request,
    x_intake_key: str | None = Header(default=None),
    settings: Settings = Depends(get_app_settings),
):
    if not settings.intake_enabled:
        raise AppError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "Not found")
    _check_key(request, x_intake_key, settings)

    engine = getattr(request.app.state, "intake_engine", None)
    key = settings.intake_encryption_key
    if engine is None or key is None:
        raise AppError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "INTAKE_STORAGE_UNAVAILABLE",
            "Email intake storage is not configured.",
        )

    too_large = AppError(
        status.HTTP_413_CONTENT_TOO_LARGE,
        "INTAKE_EVENT_TOO_LARGE",
        f"The event is larger than {settings.intake_max_request_bytes} bytes.",
    )
    declared = request.headers.get("content-length")
    if (
        declared
        and declared.isdigit()
        and int(declared) > settings.intake_max_request_bytes
    ):
        raise too_large
    body = await request.body()
    if len(body) > settings.intake_max_request_bytes:
        raise too_large

    try:
        event = IntakeEvent.model_validate_json(body)
    except ValidationError as exc:
        raise AppError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "INTAKE_EVENT_INVALID",
            _validation_problems(exc),
        ) from exc
    try:
        attachments = decode_attachments(event, settings)
    except IntakeEventInvalid as exc:
        raise AppError(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "INTAKE_EVENT_INVALID", str(exc)
        ) from exc

    stored = await asyncio.to_thread(
        insert_event,
        engine,
        event,
        attachments,
        key.get_secret_value().encode(),
    )
    # No PII: ids, counts and intents only.
    logger.info(
        "intake_event_received intake_event_id=%s duplicate=%s bd_code=%s "
        "intents=%s attachments=%d with_content=%d",
        stored.intake_event_id,
        not stored.created,
        event.sender.bd_code,
        ",".join(a.intent for a in event.actions) or "-",
        len(attachments),
        sum(1 for a in attachments if a.data is not None),
    )
    result = IntakeEventAccepted(
        intake_event_id=stored.intake_event_id,
        status=stored.status,
        duplicate=not stored.created,
    )
    if not stored.created:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT, content=result.model_dump()
        )
    return result
