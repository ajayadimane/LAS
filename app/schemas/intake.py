"""The email-intake event contract (v1): what LAS_Email pushes to us.

One event = one inbound email, after LAS_Email has checked the sender,
extracted what the mail says and classified its attachments. It describes
what the email *says*; every decision about what to do is ours
(app/services/intake/). Unknown fields are ignored so LAS_Email can add
fields without breaking us; anything we rely on is validated here.

Values in `actions` came from an LLM reading untrusted email. LAS_Email
grounds critical values (each must appear in the mail), and reports what it
could not ground in `ungrounded_fields`; treat those as missing.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_Short = Field(default=None, max_length=200)


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class IntakeSenderAuth(_Model):
    """Authentication-Results verdicts as LAS_Email read them ("pass", ...)."""

    spf: str | None = Field(default=None, max_length=40)
    dkim: str | None = Field(default=None, max_length=40)
    dmarc: str | None = Field(default=None, max_length=40)


class IntakeSender(_Model):
    email: str = Field(min_length=3, max_length=320)
    # LAS_Email's ibkart_bd_code for this sender: our BD (broker) id.
    bd_code: str | None = Field(default=None, max_length=100)
    auth: IntakeSenderAuth = Field(default_factory=IntakeSenderAuth)


class IntakeThread(_Model):
    subject: str = Field(default="", max_length=1000)
    in_reply_to: str | None = Field(default=None, max_length=998)
    references: list[str] = Field(default_factory=list, max_length=100)


class IntakeCustomer(_Model):
    name: str | None = _Short
    mobile: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=320)
    street_address: str | None = Field(default=None, max_length=500)
    city: str | None = _Short
    state_province: str | None = _Short
    postal_code: str | None = Field(default=None, max_length=20)
    services: list[str] = Field(default_factory=list, max_length=20)
    bank_name: str | None = _Short
    project_name: str | None = _Short


class IntakeCorrection(_Model):
    """A field the BD corrected, e.g. "the mobile is 98... not 97..."."""

    field: str = Field(min_length=1, max_length=40)  # name / mobile / email / services
    new: str | None = Field(default=None, max_length=500)


class IntakeAction(_Model):
    # LAS_Email's intents (NEW_CUSTOMER, SELECT_SERVICES, SUBMIT_DOCUMENT,
    # ...). Kept a plain string: a new intent there must not 422 here.
    intent: str = Field(min_length=1, max_length=40)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    grounded: bool = False
    # LAS_Email's own policy tier (AUTO / CONFIRM / REVIEW ...): advisory only.
    risk_tier: str | None = Field(default=None, max_length=20)
    ungrounded_fields: list[str] = Field(default_factory=list, max_length=100)
    customers: list[IntakeCustomer] = Field(default_factory=list, max_length=50)
    services: list[str] = Field(default_factory=list, max_length=20)
    codes: list[str] = Field(default_factory=list, max_length=50)
    attachment_ids: list[str] = Field(default_factory=list, max_length=50)
    correction: IntakeCorrection | None = None


class IntakeAttachment(_Model):
    attachment_id: str = Field(min_length=1, max_length=100)
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    doc_class: str | None = Field(default=None, max_length=40)
    class_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    av_status: str | None = Field(default=None, max_length=40)
    password_protected: bool = False
    # The file's bytes, base64. Null when LAS_Email did not send them (too
    # large, or a type we cannot store) -- the metadata still arrives.
    content_base64: str | None = None
    # Why the bytes were not sent: TOO_LARGE, TYPE_NOT_ACCEPTED, PASSWORD_PROTECTED,
    # NOT_SCANNED_CLEAN, TOTAL_TOO_LARGE, CHECKSUM_MISMATCH. Shown to the BD.
    content_omitted_reason: str | None = Field(default=None, max_length=40)


class IntakeRejectedAttachment(_Model):
    """A file LAS_Email refused before storing it (type, size, zip limits)."""

    filename: str = Field(default="", max_length=255)
    reason: str = Field(default="", max_length=300)


class IntakeEvent(_Model):
    version: Literal[1]
    event_id: str = Field(min_length=1, max_length=998)  # the Message-ID
    received_at: datetime | None = None
    sender: IntakeSender
    thread: IntakeThread = Field(default_factory=IntakeThread)
    body_text: str = Field(default="", max_length=20_000)
    language: str | None = Field(default=None, max_length=20)
    injection_suspected: bool = False
    actions: list[IntakeAction] = Field(default_factory=list, max_length=20)
    attachments: list[IntakeAttachment] = Field(default_factory=list, max_length=50)
    rejected_attachments: list[IntakeRejectedAttachment] = Field(
        default_factory=list, max_length=50
    )


class IntakeEventAccepted(BaseModel):
    intake_event_id: str
    status: str
    duplicate: bool = False
