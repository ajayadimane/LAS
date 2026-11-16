"""What we know about one emailed customer, and what is still missing.

Built from the customer an email names (LAS_Email's extraction), merged with
each reply, and checked against what registration and the chosen services
need. Values arrive from an LLM reading untrusted email; LAS_Email has
already dropped anything it could not find in the mail itself, and every
value is validated again here before it reaches the vendor.
"""

import re

from pydantic import BaseModel, EmailStr, Field, TypeAdapter, ValidationError

from app.core.config import ServiceConfig, Settings
from app.schemas.bd import CreateCustomerRequest
from app.schemas.intake import IntakeAttachment, IntakeCustomer
from app.services.service_fields import in_house

_MOBILE = re.compile(r"^[6-9]\d{9}$")
_PIN = re.compile(r"^\d{6}$")
_EMAIL = TypeAdapter(EmailStr)


class CaseDocument(BaseModel):
    """An attachment held for this case (bytes stay in intake_attachments)."""

    intake_event_id: str
    attachment_id: str
    filename: str
    content_type: str
    size_bytes: int
    doc_class: str | None = None
    has_content: bool = False
    omitted_reason: str | None = None


class CaseData(BaseModel):
    name: str | None = None
    mobile: str | None = None
    email: str | None = None
    street_address: str | None = None
    city: str | None = None
    state_province: str | None = None
    postal_code: str | None = None
    services: list[str] = Field(default_factory=list)
    # Service codes the BD asked for that cannot be taken by email (unknown,
    # or handled on a partner portal); reported to the BD once.
    unavailable_services: list[str] = Field(default_factory=list)
    # {service_code: {detail_key: value}}, e.g. DPR_ANALYSIS bank/project.
    details: dict[str, dict[str, str]] = Field(default_factory=dict)
    # Raw extracted bank/project, applied to whichever service needs them.
    bank_name: str | None = None
    project_name: str | None = None
    documents: list[CaseDocument] = Field(default_factory=list)
    existing_customer: bool = False
    # Filled once paid (Phase 2).
    paid_mode: str | None = None
    paid_amount: int | None = None
    transaction_id: str | None = None

    # -- merging -----------------------------------------------------------

    def merge_customer(
        self, c: IntakeCustomer, action_services: list[str], settings: Settings
    ) -> None:
        """Take every value the email states; a later email overrides."""
        for field in (
            "name",
            "street_address",
            "city",
            "state_province",
            "bank_name",
            "project_name",
        ):
            value = _clean(getattr(c, field))
            if value:
                setattr(self, field, value)
        mobile = normalise_mobile(c.mobile)
        if mobile:
            self.mobile = mobile
        if c.email and c.email.strip():
            self.email = c.email.strip()
        pin = re.sub(r"\D", "", c.postal_code or "")
        if pin:
            self.postal_code = pin
        self.add_services(c.services or action_services, settings)

    def apply_correction(self, field: str, new: str | None) -> None:
        """A CORRECTION's new value for name, mobile or email (services
        arrive as a service list instead)."""
        value = _clean(new)
        if not value:
            return
        if field == "name":
            self.name = value
        elif field == "mobile":
            self.mobile = normalise_mobile(value) or self.mobile
        elif field == "email":
            self.email = value

    def add_services(self, codes: list[str], settings: Settings) -> None:
        payable = {s.code for s in in_house(settings)}
        for raw in codes:
            code = raw.strip().upper()
            if code in payable:
                if code not in self.services:
                    self.services.append(code)
            elif code and code not in self.unavailable_services:
                self.unavailable_services.append(code)

    def add_documents(
        self, intake_event_id: str, attachments: list[IntakeAttachment]
    ) -> None:
        known = {(d.intake_event_id, d.attachment_id) for d in self.documents}
        for a in attachments:
            if (intake_event_id, a.attachment_id) in known:
                continue
            self.documents.append(
                CaseDocument(
                    intake_event_id=intake_event_id,
                    attachment_id=a.attachment_id,
                    filename=a.filename,
                    content_type=a.content_type,
                    size_bytes=a.size_bytes,
                    doc_class=a.doc_class,
                    # A stored event has its bytes split off into
                    # intake_attachments, so content_base64 is always null
                    # here: the bytes were received unless LAS_Email said why
                    # not. The upload re-checks the stored bytes.
                    has_content=a.content_base64 is not None
                    or a.content_omitted_reason is None,
                    omitted_reason=a.content_omitted_reason,
                )
            )

    # -- checking ----------------------------------------------------------

    def chosen_services(self, settings: Settings) -> list[ServiceConfig]:
        by_code = {s.code: s for s in in_house(settings)}
        return [by_code[c] for c in self.services if c in by_code]

    def service_details(self, settings: Settings) -> dict[str, dict[str, str]]:
        """The details each chosen service needs, from what the email said."""
        found = {"bank_name": self.bank_name, "project_name": self.project_name}
        out: dict[str, dict[str, str]] = {}
        for service in self.chosen_services(settings):
            if not service.details:
                continue
            given = self.details.get(service.code, {})
            values = {
                d.key: (given.get(d.key) or found.get(d.key) or "").strip()
                for d in service.details
            }
            out[service.code] = {k: v for k, v in values.items() if v}
        return out

    def missing(self, settings: Settings) -> list[str]:
        """What we still need, in words the BD can answer. Empty = complete."""
        out: list[str] = []
        first, last = split_name(self.name)
        if not first:
            out.append("customer's full name")
        elif len(first) > 50 or (last and len(last) > 50):
            out.append("customer's name (first and last name, at most 50 letters each)")
        if not (self.mobile and _MOBILE.match(self.mobile)):
            out.append("customer's 10-digit mobile number")
        if not _valid_email(self.email):
            out.append("customer's email address")
        if not self.street_address:
            out.append("street address")
        elif len(self.street_address) > 100:
            out.append("street address (at most 100 characters)")
        if not self.city:
            out.append("city / town")
        if not (self.state_province and len(self.state_province) >= 2):
            out.append("state")
        if not (self.postal_code and _PIN.match(self.postal_code)):
            out.append("6-digit PIN code")
        if not self.services:
            names = ", ".join(s.name for s in in_house(settings))
            out.append(f"which services the customer wants ({names})")
        details = self.service_details(settings)
        for service in self.chosen_services(settings):
            for d in service.details:
                value = details.get(service.code, {}).get(d.key)
                if not value:
                    out.append(f"{d.label.lower()} (for {service.name})")
                elif len(value) > d.max_length:
                    out.append(
                        f"{d.label.lower()} (for {service.name}, at most "
                        f"{d.max_length} characters)"
                    )
        return out

    def to_create_request(self) -> CreateCustomerRequest:
        """Only when `missing()` is empty."""
        first, last = split_name(self.name)
        return CreateCustomerRequest(
            first_name=first,
            last_name=last,
            mobile_number=self.mobile,
            email_address=self.email,
            street_address=self.street_address,
            city=self.city,
            state_province=self.state_province,
            postal_code=self.postal_code,
        )


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())
    return text or None


def _valid_email(value: str | None) -> bool:
    if not value or len(value) > 100:
        return False
    try:
        _EMAIL.validate_python(value)
    except ValidationError:
        return False
    return True


def normalise_mobile(value: str | None) -> str | None:
    """+91 98765 43210, 098765-43210, 9876543210 -> 9876543210."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if _MOBILE.match(digits) else None


def split_name(name: str | None) -> tuple[str, str | None]:
    parts = (name or "").split(None, 1)
    if not parts:
        return "", None
    return parts[0], (parts[1] if len(parts) > 1 else None)
