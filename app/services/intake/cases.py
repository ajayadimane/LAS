"""Persistence for intake cases (migrations/011_intake_cases.sql).

Synchronous SQLAlchemy, run through asyncio.to_thread by the processor. A
case's customer data is Fernet-encrypted; ref, state and routing columns are
in clear.
"""

import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from cryptography.fernet import Fernet
from sqlalchemy import text

from app.services.intake.case_data import CaseData

_REF_RE = re.compile(r"\[LAS-IN-([0-9A-F]{10})\]")


def new_ref() -> str:
    return secrets.token_hex(5).upper()


def subject_tag(ref: str) -> str:
    return f"[LAS-IN-{ref}]"


def ref_in_subject(subject: str) -> str | None:
    match = _REF_RE.search(subject or "")
    return match.group(1) if match else None


@dataclass
class Case:
    case_id: str
    ref: str
    origin_event_id: str
    bd_id: str
    bd_email: str
    person_id: str | None
    state: str
    data: CaseData
    reminders_sent: int
    expires_at: datetime | None
    last_error: str | None
    created: bool = False


_COLUMNS = (
    "case_id, ref, origin_event_id, bd_id, bd_email, person_id, state, "
    "data_ciphertext, reminders_sent, expires_at, last_error"
)


_C_COLUMNS = ", ".join("c." + c.strip() for c in _COLUMNS.split(","))


def _row_to_case(row, key: bytes, created: bool = False) -> Case:
    data = CaseData.model_validate_json(
        Fernet(key).decrypt(bytes(row["data_ciphertext"]))
    )
    return Case(
        case_id=str(row["case_id"]),
        ref=row["ref"],
        origin_event_id=str(row["origin_event_id"]),
        bd_id=row["bd_id"],
        bd_email=row["bd_email"],
        person_id=row["person_id"],
        state=row["state"],
        data=data,
        reminders_sent=row["reminders_sent"],
        expires_at=row["expires_at"],
        last_error=row["last_error"],
        created=created,
    )


def open_case(
    engine,
    *,
    origin_event_id: str,
    customer_index: int,
    bd_id: str,
    bd_email: str,
    data: CaseData,
    key: bytes,
) -> Case:
    """The case for this customer of this email, created once."""
    with engine.begin() as conn:
        row = (
            conn.execute(
                text(f"""
                    INSERT INTO intake_cases (case_id, ref, origin_event_id,
                        customer_index, bd_id, bd_email, state, data_ciphertext)
                    VALUES (:id, :ref, :event, :idx, :bd, :bd_email, 'NEW', :data)
                    ON CONFLICT (origin_event_id, customer_index) DO NOTHING
                    RETURNING {_COLUMNS}
                """),
                {
                    "id": str(uuid4()),
                    "ref": new_ref(),
                    "event": origin_event_id,
                    "idx": customer_index,
                    "bd": bd_id,
                    "bd_email": bd_email,
                    "data": Fernet(key).encrypt(data.model_dump_json().encode()),
                },
            )
            .mappings()
            .first()
        )
        if row is not None:
            return _row_to_case(row, key, created=True)
        row = (
            conn.execute(
                text(
                    f"SELECT {_COLUMNS} FROM intake_cases "
                    "WHERE origin_event_id = :event AND customer_index = :idx"
                ),
                {"event": origin_event_id, "idx": customer_index},
            )
            .mappings()
            .one()
        )
        return _row_to_case(row, key)


def save_case(engine, case: Case, key: bytes) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE intake_cases SET state = :state, person_id = :person,
                    data_ciphertext = :data, reminders_sent = :reminders,
                    expires_at = :expires, last_error = :error, updated_at = now()
                WHERE case_id = :id
            """),
            {
                "id": case.case_id,
                "state": case.state,
                "person": case.person_id,
                "data": Fernet(key).encrypt(case.data.model_dump_json().encode()),
                "reminders": case.reminders_sent,
                "expires": case.expires_at,
                "error": (case.last_error or "")[:500] or None,
            },
        )


def load_case(engine, case_id: str, key: bytes) -> Case | None:
    with engine.connect() as conn:
        row = (
            conn.execute(
                text(f"SELECT {_COLUMNS} FROM intake_cases WHERE case_id = :id"),
                {"id": case_id},
            )
            .mappings()
            .first()
        )
    return None if row is None else _row_to_case(row, key)


def find_thread_case(
    engine, *, message_ids: list[str], ref: str | None, bd_id: str, key: bytes
) -> Case | None:
    """The case a reply belongs to: by our Message-IDs it quotes, else by the
    [LAS-IN-<ref>] in its subject. Only ever this BD's own case."""
    with engine.connect() as conn:
        row = None
        if message_ids:
            row = (
                conn.execute(
                    text(f"""
                        SELECT {_C_COLUMNS}
                        FROM intake_case_messages m
                        JOIN intake_cases c ON c.case_id = m.case_id
                        WHERE m.message_id = ANY(:ids) AND c.bd_id = :bd
                        ORDER BY m.created_at DESC LIMIT 1
                    """),
                    {"ids": message_ids, "bd": bd_id},
                )
                .mappings()
                .first()
            )
        if row is None and ref:
            row = (
                conn.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM intake_cases "
                        "WHERE ref = :ref AND bd_id = :bd"
                    ),
                    {"ref": ref, "bd": bd_id},
                )
                .mappings()
                .first()
            )
    return None if row is None else _row_to_case(row, key)


def record_message(engine, case_id: str, message_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO intake_case_messages (message_id, case_id)
                VALUES (:mid, :id) ON CONFLICT (message_id) DO NOTHING
            """),
            {"mid": message_id, "id": case_id},
        )


def last_message_id(engine, case_id: str) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT message_id FROM intake_case_messages WHERE case_id = :id "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"id": case_id},
        ).scalar()


def due_info_cases(engine, *, reminder_after_hours: float) -> list[tuple[str, str]]:
    """NEEDS_INFO cases that are expired ('EXPIRE') or due a reminder
    ('REMIND'), as (case_id, what)."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT case_id,
                       CASE WHEN expires_at <= now() THEN 'EXPIRE' ELSE 'REMIND' END
                FROM intake_cases
                WHERE state = 'NEEDS_INFO' AND (
                    expires_at <= now()
                    OR (reminders_sent = 0
                        AND updated_at <= now() - make_interval(secs => :after))
                )
                ORDER BY updated_at LIMIT 50
            """),
            {"after": reminder_after_hours * 3600},
        ).all()
    return [(str(r[0]), r[1]) for r in rows]
