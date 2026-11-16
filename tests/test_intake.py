"""POST /internal/v1/intake/events: LAS_Email pushes one event per email.

Storage tests need the throwaway Postgres (see tests/conftest.py's
DPR_TEST_DATABASE_URL note); they are skipped without it. Auth and
enablement tests run without a database.
"""

import base64
import hashlib
import json

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from app.main import create_app
from app.services.intake.store import list_events, load_attachment, load_event
from tests.conftest import DPR_TEST_DSN

URL = "/internal/v1/intake/events"
KEY = "test-intake-key"
ENC_KEY = Fernet.generate_key().decode()
MOBILE = "9876543210"
PDF = b"%PDF-1.4 test document"


def _event(**overrides) -> dict:
    event = {
        "version": 1,
        "event_id": "<abc123@mail.example.com>",
        "received_at": "2026-11-16T10:00:00+05:30",
        "sender": {
            "email": "BD.One@example.com",
            "bd_code": "97315",
            "auth": {"spf": "pass", "dkim": "pass", "dmarc": "pass"},
        },
        "thread": {"subject": "New customer", "in_reply_to": None, "references": []},
        "body_text": f"Please onboard Ramesh Patil {MOBILE} for DPR analysis.",
        "language": "en",
        "injection_suspected": False,
        "actions": [
            {
                "intent": "NEW_CUSTOMER",
                "confidence": 0.93,
                "grounded": True,
                "ungrounded_fields": [],
                "customers": [
                    {
                        "name": "Ramesh Patil",
                        "mobile": MOBILE,
                        "email": "ramesh@example.com",
                        "services": ["DPR_ANALYSIS"],
                        "bank_name": "SBI",
                        "project_name": "Dairy unit",
                    }
                ],
                "attachment_ids": ["1"],
            }
        ],
        "attachments": [
            {
                "attachment_id": "1",
                "filename": "dpr.pdf",
                "content_type": "application/pdf",
                "size_bytes": len(PDF),
                "sha256": hashlib.sha256(PDF).hexdigest(),
                "doc_class": "DPR",
                "class_confidence": 0.9,
                "av_status": "CLEAN",
                "password_protected": False,
                "content_base64": base64.b64encode(PDF).decode(),
            }
        ],
    }
    event.update(overrides)
    return event


def _headers(key: str = KEY) -> dict:
    return {"X-Intake-Key": key, "Content-Type": "application/json"}


@pytest.fixture
def intake_settings(settings):
    return settings.model_copy(
        update={
            "intake_enabled": True,
            "intake_api_key": SecretStr(KEY),
            "intake_encryption_key": SecretStr(ENC_KEY),
            "dpr_database_url": SecretStr(DPR_TEST_DSN),
            # These tests are about receiving; the processor has its own.
            "intake_worker_enabled": False,
        }
    )


@pytest.fixture
def intake_db(dpr_pg_engine):
    with dpr_pg_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE intake_case_messages, intake_cases, intake_attachments, "
                "intake_events"
            )
        )
    return dpr_pg_engine


@pytest.fixture
def intake_client(intake_settings, intake_db):
    with TestClient(create_app(intake_settings)) as c:
        yield c


# --- enablement and auth (no database needed) ----------------------------


def test_disabled_route_is_not_found_even_with_the_key(client):
    r = client.post(URL, json=_event(), headers=_headers())
    assert r.status_code == 404


@pytest.fixture
def no_db_client(intake_settings):
    s = intake_settings.model_copy(update={"dpr_database_url": None})
    with TestClient(create_app(s)) as c:
        yield c


@pytest.mark.parametrize("key", [None, "", "wrong-key"])
def test_missing_or_wrong_key_is_forbidden(no_db_client, key):
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["X-Intake-Key"] = key
    r = no_db_client.post(URL, json=_event(), headers=headers)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_without_storage_the_route_answers_503_so_las_email_retries(no_db_client):
    r = no_db_client.post(URL, json=_event(), headers=_headers())
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "INTAKE_STORAGE_UNAVAILABLE"


# --- storing events -------------------------------------------------------


def test_event_is_stored_encrypted_and_readable_back(intake_client, intake_db):
    r = intake_client.post(URL, json=_event(), headers=_headers())
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "RECEIVED"
    assert body["duplicate"] is False
    event_id = body["intake_event_id"]

    rows = list_events(intake_db)
    assert len(rows) == 1
    assert rows[0]["sender_email"] == "bd.one@example.com"  # lower-cased
    assert rows[0]["bd_code"] == "97315"

    with intake_db.connect() as conn:
        raw = conn.execute(text("SELECT event_ciphertext FROM intake_events")).scalar()
        att_raw = conn.execute(
            text("SELECT ciphertext FROM intake_attachments")
        ).scalar()
    assert MOBILE.encode() not in bytes(raw)  # PII is not stored in clear
    assert PDF not in bytes(att_raw)

    key = ENC_KEY.encode()
    event = load_event(intake_db, event_id, key)
    assert event.actions[0].customers[0].mobile == MOBILE
    # The bytes live only in intake_attachments, never inside the event body.
    assert event.attachments[0].content_base64 is None
    assert load_attachment(intake_db, event_id, "1", key) == PDF


def test_a_repeated_message_id_is_409_with_the_original_id(intake_client, intake_db):
    first = intake_client.post(URL, json=_event(), headers=_headers()).json()
    r = intake_client.post(URL, json=_event(body_text="changed"), headers=_headers())
    assert r.status_code == 409
    assert r.json()["intake_event_id"] == first["intake_event_id"]
    assert r.json()["duplicate"] is True
    assert len(list_events(intake_db)) == 1


def test_previous_key_is_accepted_during_rotation(intake_settings, intake_db):
    s = intake_settings.model_copy(
        update={
            "intake_api_key": SecretStr("new-key"),
            "intake_api_key_previous": SecretStr(KEY),
        }
    )
    with TestClient(create_app(s)) as c:
        assert c.post(URL, json=_event(), headers=_headers(KEY)).status_code == 202


def test_metadata_only_attachment_is_kept_without_bytes(intake_client, intake_db):
    event = _event()
    big = event["attachments"][0]
    big.update(
        {"content_base64": None, "size_bytes": 9_000_000, "filename": "scan.docx"}
    )
    r = intake_client.post(URL, json=event, headers=_headers())
    assert r.status_code == 202
    with intake_db.connect() as conn:
        row = conn.execute(
            text("SELECT size_bytes, ciphertext FROM intake_attachments")
        ).one()
    assert row.size_bytes == 9_000_000
    assert row.ciphertext is None


def test_unknown_fields_are_ignored_for_forward_compatibility(intake_client):
    event = _event(new_field_from_las_email={"anything": 1})
    event["actions"][0]["intent"] = "SOME_FUTURE_INTENT"
    assert intake_client.post(URL, json=event, headers=_headers()).status_code == 202


# --- rejected events --------------------------------------------------------


def _attachment_overrides(**changes) -> dict:
    event = _event()
    event["attachments"][0].update(changes)
    return event


@pytest.mark.parametrize(
    "event",
    [
        _attachment_overrides(content_base64="not base64!!"),
        _attachment_overrides(sha256="0" * 64),
        _attachment_overrides(size_bytes=len(PDF) + 1),
        _event(
            attachments=[_event()["attachments"][0], _event()["attachments"][0]]
        ),  # duplicate attachment_id
    ],
    ids=["bad-base64", "sha-mismatch", "size-mismatch", "duplicate-id"],
)
def test_attachment_problems_are_422(intake_client, intake_db, event):
    r = intake_client.post(URL, json=event, headers=_headers())
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INTAKE_EVENT_INVALID"
    assert list_events(intake_db) == []


def test_too_many_attachments_is_422(intake_settings, intake_db):
    s = intake_settings.model_copy(update={"intake_max_attachments": 0})
    with TestClient(create_app(s)) as c:
        r = c.post(URL, json=_event(), headers=_headers())
    assert r.status_code == 422
    assert "Too many attachments" in r.json()["error"]["message"]


def test_schema_errors_are_422_without_echoing_the_email(intake_client):
    event = _event(version=2)
    event["actions"][0]["confidence"] = 7  # out of range
    r = intake_client.post(URL, json=event, headers=_headers())
    assert r.status_code == 422
    message = r.json()["error"]["message"]
    assert "version" in message and "confidence" in message
    assert MOBILE not in r.text


def test_not_json_is_422(intake_client):
    r = intake_client.post(URL, content=b"not json", headers=_headers())
    assert r.status_code == 422


def test_oversized_request_is_413(intake_settings, intake_db):
    s = intake_settings.model_copy(update={"intake_max_request_bytes": 1024})
    payload = json.dumps(_event(body_text="x" * 5000)).encode()
    with TestClient(create_app(s)) as c:
        r = c.post(URL, content=payload, headers=_headers())
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "INTAKE_EVENT_TOO_LARGE"


def test_event_shaped_like_las_email_dispatcher_output(intake_client, intake_db):
    """Mirrors LAS_Email's las/core_handoff.py:build_event, field for field."""
    event = _event()
    event["received_at"] = "2026-11-16T04:30:00+00:00"
    event["actions"][0].update(
        {"risk_tier": "AUTO", "ungrounded_fields": ["customers.0.email"], "codes": []}
    )
    event["actions"][0]["customers"][0].update(
        {
            "street_address": "12 MG Road",
            "city": "Bengaluru",
            "state_province": "Karnataka",
            "postal_code": "560001",
            "email": None,
        }
    )
    event["attachments"].append(
        {
            "attachment_id": "2",
            "filename": "land.docx",
            "content_type": "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document",
            "size_bytes": 3_000_000,
            "sha256": "b" * 64,
            "doc_class": "LAND_RECORD",
            "class_confidence": 0.81,
            "av_status": "CLEAN",
            "password_protected": False,
            "content_base64": None,
            "content_omitted_reason": "TYPE_NOT_ACCEPTED",
        }
    )
    r = intake_client.post(URL, json=event, headers=_headers())
    assert r.status_code == 202, r.text
    stored = load_event(intake_db, r.json()["intake_event_id"], ENC_KEY.encode())
    assert stored.actions[0].risk_tier == "AUTO"
    assert stored.actions[0].ungrounded_fields == ["customers.0.email"]
    assert stored.attachments[1].content_omitted_reason == "TYPE_NOT_ACCEPTED"
    assert (
        load_attachment(intake_db, r.json()["intake_event_id"], "2", ENC_KEY.encode())
        is None
    )
