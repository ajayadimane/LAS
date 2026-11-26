"""Encrypted, short-lived staging for an uploaded document's bytes.

The Wallet write happens synchronously in the upload request (unchanged); a
worker process picks up the analysis later, and needs the same bytes to do
it. This is the only place raw document content is held durably -- always
encrypted at rest, and deleted as soon as the worker that reads it reaches a
terminal state (see worker.py). Never queried for content outside that one
reader.
"""

import json

from cryptography.fernet import Fernet
from sqlalchemy import text

from app.services.documents import StoredFile


def stage_upload(
    engine,
    run_id: str,
    file: StoredFile,
    key: bytes,
    project_context: dict[str, str] | None = None,
) -> None:
    """`project_context`: the DPR Analysis service details (bank name,
    project name), encrypted like the bytes (migration 012)."""
    fernet = Fernet(key)
    ciphertext = fernet.encrypt(file.data)
    context_ciphertext = (
        fernet.encrypt(json.dumps(project_context).encode())
        if project_context
        else None
    )
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO dpr_upload_staging
                    (run_id, ciphertext, filename, content_type,
                     project_context_ciphertext)
                VALUES (:run_id, :ciphertext, :filename, :content_type, :context)
                ON CONFLICT (run_id) DO UPDATE
                  SET ciphertext = EXCLUDED.ciphertext,
                      filename = EXCLUDED.filename,
                      content_type = EXCLUDED.content_type,
                      project_context_ciphertext = EXCLUDED.project_context_ciphertext,
                      created_at = now()
            """),
            {
                "run_id": run_id,
                "ciphertext": ciphertext,
                "filename": file.name,
                "content_type": file.content_type,
                "context": context_ciphertext,
            },
        )


def load_staged_project_context(engine, run_id: str, key: bytes) -> dict[str, str]:
    """The staged service details, or {} (none staged, or a pre-012 row)."""
    with engine.connect() as conn:
        value = conn.execute(
            text(
                "SELECT project_context_ciphertext FROM dpr_upload_staging "
                "WHERE run_id=:run_id"
            ),
            {"run_id": run_id},
        ).scalar()
    if not value:
        return {}
    return json.loads(Fernet(key).decrypt(bytes(value)))


def load_staged_upload(engine, run_id: str, key: bytes) -> StoredFile | None:
    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT ciphertext, filename, content_type FROM dpr_upload_staging "
                    "WHERE run_id=:run_id"
                ),
                {"run_id": run_id},
            )
            .mappings()
            .first()
        )
    if row is None:
        return None
    data = Fernet(key).decrypt(bytes(row["ciphertext"]))
    return StoredFile(name=row["filename"], content_type=row["content_type"], data=data)


def delete_staged_upload(engine, run_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM dpr_upload_staging WHERE run_id=:run_id"),
            {"run_id": run_id},
        )


def delete_staged_upload_if_terminal(engine, run_id: str) -> bool:
    """Deletes a run's staged bytes only once the run is COMPLETED or FAILED.
    The worker's own cleanup goes through this, so a worker that lost the
    claim race, or handed its run back on shutdown, never deletes the bytes
    another attempt still needs."""
    with engine.begin() as conn:
        row = conn.execute(
            text("""
                DELETE FROM dpr_upload_staging s
                USING dpr_analysis_runs r
                WHERE s.run_id = :run_id AND r.run_id = s.run_id
                  AND r.status IN ('COMPLETED', 'FAILED')
                RETURNING s.run_id
            """),
            {"run_id": run_id},
        ).scalar_one_or_none()
    return row is not None


def purge_terminal_staging(engine) -> int:
    """Sweeps staged bytes left behind for runs that already finished (a
    worker crash between finish() and its cleanup). Nothing staged may
    outlive processing."""
    with engine.begin() as conn:
        return conn.execute(
            text("""
                DELETE FROM dpr_upload_staging s
                USING dpr_analysis_runs r
                WHERE r.run_id = s.run_id
                  AND r.status IN ('COMPLETED', 'FAILED')
            """)
        ).rowcount
