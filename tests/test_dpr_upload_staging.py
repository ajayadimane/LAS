"""Tests for app/services/dpr_agentic/upload_staging.py.

See tests/conftest.py for the Postgres fixtures these need.
"""

from cryptography.fernet import Fernet, InvalidToken

from app.services.documents import StoredFile
from app.services.dpr_agentic.jobs import submit
from app.services.dpr_agentic.upload_staging import (
    delete_staged_upload,
    load_staged_upload,
    stage_upload,
)

KEY = Fernet.generate_key()


def _run(engine, **overrides):
    defaults = dict(
        service_request_id="SR-1",
        document_sha256="d" * 64,
        source_reference="wallet-person-custom-field:9000000013:GEO_CHECK",
    )
    defaults.update(overrides)
    run_id, _ = submit(engine, **defaults)
    return run_id


def test_round_trips_the_exact_bytes(dpr_pg):
    run_id = _run(dpr_pg)
    file = StoredFile(
        name="report.pdf", content_type="application/pdf", data=b"%PDF- hello"
    )
    stage_upload(dpr_pg, run_id, file, KEY)

    loaded = load_staged_upload(dpr_pg, run_id, KEY)

    assert loaded == file


def test_is_encrypted_at_rest(dpr_pg):
    """The plaintext must never be readable straight from the column."""
    run_id = _run(dpr_pg)
    file = StoredFile(
        name="report.pdf", content_type="application/pdf", data=b"secret content"
    )
    stage_upload(dpr_pg, run_id, file, KEY)

    from sqlalchemy import text

    with dpr_pg.connect() as conn:
        raw = conn.execute(
            text("SELECT ciphertext FROM dpr_upload_staging WHERE run_id=:id"),
            {"id": run_id},
        ).scalar_one()
    assert b"secret content" not in bytes(raw)


def test_wrong_key_cannot_decrypt(dpr_pg):
    run_id = _run(dpr_pg)
    file = StoredFile(name="x.pdf", content_type="application/pdf", data=b"data")
    stage_upload(dpr_pg, run_id, file, KEY)

    other_key = Fernet.generate_key()
    try:
        load_staged_upload(dpr_pg, run_id, other_key)
        raised = False
    except InvalidToken:
        raised = True
    assert raised


def test_load_returns_none_when_nothing_staged(dpr_pg):
    run_id = _run(dpr_pg)
    assert load_staged_upload(dpr_pg, run_id, KEY) is None


def test_delete_removes_it(dpr_pg):
    run_id = _run(dpr_pg)
    file = StoredFile(name="x.pdf", content_type="application/pdf", data=b"data")
    stage_upload(dpr_pg, run_id, file, KEY)

    delete_staged_upload(dpr_pg, run_id)

    assert load_staged_upload(dpr_pg, run_id, KEY) is None


def test_staging_twice_replaces_the_first_upload(dpr_pg):
    run_id = _run(dpr_pg)
    stage_upload(
        dpr_pg,
        run_id,
        StoredFile(name="a.pdf", content_type="application/pdf", data=b"first"),
        KEY,
    )
    stage_upload(
        dpr_pg,
        run_id,
        StoredFile(name="b.pdf", content_type="application/pdf", data=b"second"),
        KEY,
    )

    loaded = load_staged_upload(dpr_pg, run_id, KEY)

    assert loaded.name == "b.pdf"
    assert loaded.data == b"second"
