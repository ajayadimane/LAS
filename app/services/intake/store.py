"""Persistence for intake events (migrations/010_intake_events.sql).

Synchronous SQLAlchemy, like app/services/dpr_agentic/jobs.py: callers on
the event loop run these through asyncio.to_thread. The event body and the
attachment bytes are Fernet-encrypted before they reach the database.
"""

from dataclasses import dataclass
from uuid import uuid4

from cryptography.fernet import Fernet
from sqlalchemy import text

from app.schemas.intake import IntakeEvent
from app.services.intake.events import DecodedAttachment, event_without_content


@dataclass(frozen=True)
class StoredEvent:
    intake_event_id: str
    status: str
    created: bool


def insert_event(
    engine,
    event: IntakeEvent,
    attachments: list[DecodedAttachment],
    key: bytes,
) -> StoredEvent:
    """Store the event once. A repeat of the same Message-ID returns the
    existing row with created=False and writes nothing."""
    fernet = Fernet(key)
    new_id = str(uuid4())
    with engine.begin() as conn:
        row = conn.execute(
            text("""
                INSERT INTO intake_events (intake_event_id, event_id, sender_email,
                    bd_code, received_at, event_ciphertext)
                VALUES (:id, :event_id, :sender, :bd_code, :received_at, :ciphertext)
                ON CONFLICT (event_id) DO NOTHING
                RETURNING intake_event_id
            """),
            {
                "id": new_id,
                "event_id": event.event_id,
                "sender": event.sender.email.lower(),
                "bd_code": event.sender.bd_code,
                "received_at": event.received_at,
                "ciphertext": fernet.encrypt(event_without_content(event)),
            },
        ).first()
        if row is None:
            existing = (
                conn.execute(
                    text(
                        "SELECT intake_event_id, status FROM intake_events "
                        "WHERE event_id = :event_id"
                    ),
                    {"event_id": event.event_id},
                )
                .mappings()
                .one()
            )
            return StoredEvent(
                str(existing["intake_event_id"]), existing["status"], created=False
            )
        for att in attachments:
            conn.execute(
                text("""
                    INSERT INTO intake_attachments (intake_event_id, attachment_id,
                        filename, content_type, size_bytes, sha256, doc_class,
                        ciphertext)
                    VALUES (:id, :attachment_id, :filename, :content_type, :size,
                        :sha256, :doc_class, :ciphertext)
                """),
                {
                    "id": new_id,
                    "attachment_id": att.attachment_id,
                    "filename": att.filename,
                    "content_type": att.content_type,
                    "size": att.size_bytes,
                    "sha256": att.sha256,
                    "doc_class": att.doc_class,
                    "ciphertext": None
                    if att.data is None
                    else fernet.encrypt(att.data),
                },
            )
    return StoredEvent(new_id, "RECEIVED", created=True)


def load_event(engine, intake_event_id: str, key: bytes) -> IntakeEvent | None:
    """The stored event (attachment bytes not included)."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT event_ciphertext FROM intake_events WHERE intake_event_id = :id"
            ),
            {"id": intake_event_id},
        ).first()
    if row is None:
        return None
    return IntakeEvent.model_validate_json(Fernet(key).decrypt(bytes(row[0])))


def load_attachment(
    engine, intake_event_id: str, attachment_id: str, key: bytes
) -> bytes | None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT ciphertext FROM intake_attachments "
                "WHERE intake_event_id = :id AND attachment_id = :att"
            ),
            {"id": intake_event_id, "att": attachment_id},
        ).first()
    if row is None or row[0] is None:
        return None
    return Fernet(key).decrypt(bytes(row[0]))


def list_events(engine, limit: int = 20) -> list[dict]:
    """Newest first, routing columns only (no decryption)."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT intake_event_id, event_id, sender_email, bd_code,
                       received_at, status, created_at
                FROM intake_events ORDER BY created_at DESC LIMIT :limit
            """),
            {"limit": limit},
        ).mappings()
        return [dict(r) for r in rows]


# -- the worker's queue ------------------------------------------------------

LEASE_SECONDS = 300
MAX_ATTEMPTS = 5


def claim_events(engine, limit: int = 10) -> list[str]:
    """Take due RECEIVED events (and PROCESSING ones whose lease ran out).

    `lease_until` on a RECEIVED event is its not-before time after a failed
    attempt (see `release_event`).
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                UPDATE intake_events SET status = 'PROCESSING',
                    attempts = attempts + 1,
                    lease_until = now() + make_interval(secs => :lease),
                    updated_at = now()
                WHERE intake_event_id IN (
                    SELECT intake_event_id FROM intake_events
                    WHERE (status = 'RECEIVED'
                           AND (lease_until IS NULL OR lease_until <= now()))
                       OR (status = 'PROCESSING' AND lease_until < now())
                    ORDER BY created_at
                    LIMIT :limit
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING intake_event_id
            """),
            {"lease": LEASE_SECONDS, "limit": limit},
        ).all()
    return [str(r[0]) for r in rows]


def finish_event(
    engine, intake_event_id: str, status: str, note: str | None = None
) -> None:
    """PROCESSED or IGNORED (with why), or FAILED."""
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE intake_events SET status = :status, lease_until = NULL,
                    last_error = :note, updated_at = now()
                WHERE intake_event_id = :id
            """),
            {
                "id": intake_event_id,
                "status": status,
                "note": (note or "")[:500] or None,
            },
        )


def release_event(engine, intake_event_id: str, error: str) -> str:
    """After an unexpected failure: retry later with backoff, or give up."""
    with engine.begin() as conn:
        status = conn.execute(
            text("""
                UPDATE intake_events SET
                    status = CASE WHEN attempts >= :max THEN 'FAILED'
                        ELSE 'RECEIVED' END,
                    lease_until = CASE WHEN attempts >= :max THEN NULL
                        ELSE now() + make_interval(secs => 60 * power(2, attempts))
                    END,
                    last_error = :error, updated_at = now()
                WHERE intake_event_id = :id
                RETURNING status
            """),
            {"id": intake_event_id, "max": MAX_ATTEMPTS, "error": error[:500]},
        ).scalar()
    return str(status)


UNKNOWN_SENDER_REPLIED = "UNKNOWN_SENDER_REPLIED"


def replied_to_unknown_sender_recently(engine, sender: str, hours: float = 24) -> bool:
    """Did we already send this unknown sender the "not a registered BD"
    reply within `hours`?"""
    with engine.connect() as conn:
        return (
            conn.execute(
                text("""
                    SELECT 1 FROM intake_events
                    WHERE sender_email = :sender AND last_error = :marker
                      AND updated_at > now() - make_interval(secs => :secs)
                    LIMIT 1
                """),
                {
                    "sender": sender,
                    "marker": UNKNOWN_SENDER_REPLIED,
                    "secs": hours * 3600,
                },
            ).first()
            is not None
        )
