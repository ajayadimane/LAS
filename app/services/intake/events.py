"""Checking an intake event before it is stored.

Everything here is about the envelope, not the meaning: attachment bytes
must decode and match the size and SHA-256 LAS_Email declared, ids must be
unique, and the count must stay within our limit. Whether the email makes
sense as a request is decided later, by the processor.
"""

import base64
import binascii
import hashlib
from dataclasses import dataclass

from app.core.config import Settings
from app.schemas.intake import IntakeEvent


class IntakeEventInvalid(ValueError):
    """The event is malformed; LAS_Email must not resend it unchanged."""


@dataclass(frozen=True)
class DecodedAttachment:
    attachment_id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    doc_class: str | None
    data: bytes | None  # None when LAS_Email sent metadata only


def decode_attachments(
    event: IntakeEvent, settings: Settings
) -> list[DecodedAttachment]:
    if len(event.attachments) > settings.intake_max_attachments:
        raise IntakeEventInvalid(
            f"Too many attachments: {len(event.attachments)} "
            f"(max {settings.intake_max_attachments})."
        )
    seen: set[str] = set()
    decoded: list[DecodedAttachment] = []
    for att in event.attachments:
        if att.attachment_id in seen:
            raise IntakeEventInvalid(f"Duplicate attachment_id {att.attachment_id!r}.")
        seen.add(att.attachment_id)
        data = None
        if att.content_base64 is not None:
            try:
                data = base64.b64decode(att.content_base64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise IntakeEventInvalid(
                    f"Attachment {att.attachment_id!r} is not valid base64."
                ) from exc
            if len(data) != att.size_bytes:
                raise IntakeEventInvalid(
                    f"Attachment {att.attachment_id!r} is {len(data)} bytes, "
                    f"not the declared {att.size_bytes}."
                )
            if hashlib.sha256(data).hexdigest() != att.sha256:
                raise IntakeEventInvalid(
                    f"Attachment {att.attachment_id!r} does not match its SHA-256."
                )
        decoded.append(
            DecodedAttachment(
                attachment_id=att.attachment_id,
                filename=att.filename,
                content_type=att.content_type,
                size_bytes=att.size_bytes,
                sha256=att.sha256,
                doc_class=att.doc_class,
                data=data,
            )
        )
    return decoded


def event_without_content(event: IntakeEvent) -> bytes:
    """The event as JSON, minus attachment bytes (those are stored apart)."""
    slim = event.model_copy(
        update={
            "attachments": [
                a.model_copy(update={"content_base64": None}) for a in event.attachments
            ]
        }
    )
    return slim.model_dump_json().encode()
