"""The public lead form (user, 2026-11-18): GET/POST /leads/intake/{token}.

Opened from the "not a registered BD" reply. The signed token is the only
gate (it names the sender and is single use). Returns HTML pages, not JSON,
so it lives off /api/v1 like the other public routes. The body is parsed by
hand (urllib) to avoid adding python-multipart for one urlencoded form.
"""

import asyncio
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.api.deps import core_db_engine, get_app_settings
from app.core.config import Settings
from app.core.logging import get_logger
from app.services.intake import leads
from app.services.mailer import MailNotConfiguredError, MailSendError

logger = get_logger(__name__)

router = APIRouter(prefix="/leads", tags=["leads"], include_in_schema=False)

_env = Environment(
    loader=FileSystemLoader(
        Path(__file__).resolve().parents[1] / "templates" / "leads"
    ),
    autoescape=select_autoescape(["html", "j2"]),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)
_NO_STORE = {"Cache-Control": "no-store"}


def _message(title: str, lines: list[str], status: int = 200) -> HTMLResponse:
    html = _env.get_template("message.html.j2").render(title=title, lines=lines)
    return HTMLResponse(html, status_code=status, headers=_NO_STORE)


def _form(action: str, values: dict, errors: dict, status: int = 200) -> HTMLResponse:
    html = _env.get_template("intake_form.html.j2").render(
        action=action,
        states=[],  # no district list in Core: plain text boxes
        districts=[],
        consent_text=leads.CONSENT_TEXT,
        values=values,
        errors=errors,
    )
    return HTMLResponse(html, status_code=status, headers=_NO_STORE)


def _expired() -> HTMLResponse:
    return _message(
        "This link has expired",
        ["Please email us again and we will send you a new link."],
        status=404,
    )


@router.get("/intake/{token}", response_class=HTMLResponse)
async def form_page(
    token: str,
    request: Request,
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    if leads.read_token(settings, token) is None:
        return _expired()
    return _form(
        "",
        {"mobile": "", "state": "", "district": "", "pincode": "", "consent": False},
        {},
    )


@router.post("/intake/{token}", response_class=HTMLResponse)
async def submit_form(
    token: str,
    request: Request,
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    claims = leads.read_token(settings, token)
    if claims is None:
        return _expired()
    body = (await request.body())[:10_000].decode("utf-8", "replace")
    posted = {k: v[0] for k, v in parse_qs(body).items() if v}
    values = {
        "mobile": posted.get("mobile", ""),
        "state": posted.get("state", ""),
        "district": posted.get("district", ""),
        "pincode": posted.get("pincode", ""),
        "consent": "consent" in posted,
    }
    fields, errors = leads.validate_form(**values)
    if fields is None:
        return _form("", values, errors, status=422)

    engine = core_db_engine(request)
    if engine is None:
        return _message(
            "We could not save your request",
            ["Please try again later."],
            status=503,
        )
    sender = str(claims["sender"])
    lead_id = await asyncio.to_thread(
        leads.save_lead, engine, str(claims["tid"]), sender, fields
    )
    if lead_id is None:
        return _message(
            "This request was already sent",
            ["We have your details already. An advisor will contact you."],
        )
    await _notify(request, settings, lead_id, sender, fields, engine)
    return _message(
        "Thanks — we've received your request",
        ["An advisor will contact you soon."],
    )


async def _notify(request, settings, lead_id, sender, fields, engine) -> None:
    """Best effort: the lead is already saved, a failed mail must not lose it."""
    mailer = getattr(request.app.state, "mailer", None)
    if mailer is None:
        return
    try:
        if settings.intake_lead_notify_email:
            await mailer.send(
                leads.admin_mail(settings.intake_lead_notify_email, sender, fields)
            )
            await asyncio.to_thread(leads.mark_notified, engine, lead_id)
        await mailer.send(leads.confirmation_mail(sender))
    except (MailNotConfiguredError, MailSendError) as exc:
        logger.warning(
            "lead_mail_not_sent lead=%s error=%s", lead_id, type(exc).__name__
        )
