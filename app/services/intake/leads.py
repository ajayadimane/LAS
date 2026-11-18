"""Lead capture for a sender who is not a registered BD (user, 2026-11-18).

The old LAS_Email flow, cut down to what Core can do: the "not a registered
BD" reply carries a signed link to a details form (mobile, state, district,
PIN code, consent). A submission is stored (`intake_leads`) and emailed to the
LAS Admin (`INTAKE_LEAD_NOTIFY_EMAIL`), who routes it to a BD by hand; the
sender gets a confirmation. One link, one submission.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from html import escape

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.core.signed_tokens import sign, unsign
from app.services.intake.case_data import normalise_mobile
from app.services.mailer import Mail

TOKEN_TYPE = "lead_form"  # noqa: S105 -- a token discriminator, not a credential
PINCODE_RE = re.compile(r"^[1-9][0-9]{5}$")

# Shown on the form and hashed into `consent_sha256`, so a later dispute can
# show exactly what was agreed to. Change the text = change the version.
CONSENT_TEXT = (
    "To connect you with an advisor, Integra Micro Systems will use your mobile "
    "number, state and district. We share these only with the advisor assigned "
    "to your district, who will contact you about agri loan advisory. We will "
    "not use your number for unrelated marketing. We keep these details for up "
    "to 6 months if you don't go ahead, then delete them. We do not ask for "
    "Aadhaar, PAN, bank or land documents at this step."
)
CONSENT_SHA256 = hashlib.sha256(CONSENT_TEXT.encode()).hexdigest()


def _secret(settings: Settings) -> bytes | None:
    key = settings.intake_encryption_key
    if key is None or not key.get_secret_value():
        return None
    return hashlib.sha256(b"lead-form:" + key.get_secret_value().encode()).digest()


def form_link(settings: Settings, sender: str) -> str | None:
    """The signed link for this sender's form, or None if not configured."""
    secret = _secret(settings)
    if secret is None or not settings.intake_public_base_url or not sender:
        return None
    token = sign(
        {"sender": sender, "tid": secrets.token_urlsafe(12)},
        secret,
        TOKEN_TYPE,
        int(settings.intake_lead_link_hours * 3600),
    )
    base = settings.intake_public_base_url.rstrip("/")
    return f"{base}{settings.context_path}/leads/intake/{token}"


def read_token(settings: Settings, token: str) -> dict | None:
    secret = _secret(settings)
    claims = unsign(token, secret, TOKEN_TYPE) if secret else None
    if not claims or not claims.get("sender") or not claims.get("tid"):
        return None
    return claims


@dataclass(frozen=True)
class LeadFields:
    mobile: str
    state: str
    district: str
    pincode: str | None


def validate_form(
    *, mobile: str, state: str, district: str, pincode: str, consent: bool
) -> tuple[LeadFields | None, dict[str, str]]:
    """The form's input is attacker-controlled: check every field here."""
    errors: dict[str, str] = {}
    number = normalise_mobile(mobile)
    if not number:
        errors["mobile"] = "Please enter a valid 10-digit mobile number."
    state = " ".join(state.split())[:80]
    district = " ".join(district.split())[:80]
    pincode = "".join(pincode.split())
    if not state:
        errors["state"] = "Please choose your state."
    if not district:
        errors["district"] = (
            "Please choose your district so we can find an advisor near you."
        )
    if pincode and not PINCODE_RE.match(pincode):
        errors["pincode"] = "Please enter a valid 6-digit PIN code, or leave it blank."
    if not consent:
        errors["consent"] = "Please tick the box to let an advisor contact you."
    if errors or number is None:
        return None, errors
    return LeadFields(number, state, district, pincode or None), {}


def save_lead(engine, token_id: str, sender: str, fields: LeadFields) -> int | None:
    """Store the lead; None if this link was already used."""
    try:
        with engine.begin() as conn:
            return int(
                conn.execute(
                    text("""
                        INSERT INTO intake_leads (token_id, sender_email, mobile,
                            state, district, pincode, consent_sha256)
                        VALUES (:tid, :sender, :mobile, :state, :district,
                            :pincode, :consent)
                        RETURNING lead_id
                    """),
                    {
                        "tid": token_id,
                        "sender": sender,
                        "mobile": fields.mobile,
                        "state": fields.state,
                        "district": fields.district,
                        "pincode": fields.pincode,
                        "consent": CONSENT_SHA256,
                    },
                ).scalar_one()
            )
    except IntegrityError:
        return None


def mark_notified(engine, lead_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE intake_leads SET admin_notified = TRUE WHERE lead_id = :id"),
            {"id": lead_id},
        )


_FOOTER = "This is an automated message from LAS."


def admin_mail(to: str, sender: str, fields: LeadFields) -> Mail:
    rows = [
        ("Mobile", fields.mobile),
        ("State", fields.state),
        ("District", fields.district),
        ("PIN code", fields.pincode or "-"),
        ("Emailed from", sender),
    ]
    text_body = "\n".join(
        [
            "A person who is not a registered BD asked to be connected with a "
            "Business Associate Executive. They agreed to be contacted.",
            "",
            *(f"{k}: {v}" for k, v in rows),
            "",
            "Please route this to a BD for the district.",
            "",
            _FOOTER,
        ]
    )
    table = "".join(
        f"<tr><td style='padding:2px 12px 2px 0;color:#6b7280'>{escape(k)}</td>"
        f"<td>{escape(v)}</td></tr>"
        for k, v in rows
    )
    html = (
        '<div style="font-family:Arial,sans-serif;font-size:14px;color:#1f2937">'
        "<p>A person who is not a registered BD asked to be connected with a "
        "Business Associate Executive. They agreed to be contacted.</p>"
        f"<table>{table}</table><p>Please route this to a BD for the district.</p>"
        f'<p style="color:#6b7280;font-size:12px">{_FOOTER}</p></div>'
    )
    return Mail(
        to=to,
        subject=f"LAS lead: {fields.district}, {fields.state} ({fields.mobile})",
        text=text_body,
        html=html,
        auto_submitted=True,
    )


def confirmation_mail(to: str) -> Mail:
    message = (
        "Thank you. We have received your request and will connect you with a "
        "Business Associate Executive near you."
    )
    return Mail(
        to=to,
        subject="LAS: we have received your request",
        text=f"Hello,\n\n{message}\n\nRegards,\nLAS Team, Integra Micro\n\n{_FOOTER}",
        html=(
            '<div style="font-family:Arial,sans-serif;font-size:14px;color:#1f2937">'
            f"<p>Hello,</p><p>{escape(message)}</p>"
            "<p>Regards,<br>LAS Team, Integra Micro</p>"
            f'<p style="color:#6b7280;font-size:12px">{_FOOTER}</p></div>'
        ),
        auto_submitted=True,
    )
