"""Encrypted, short-lived staging for an uploaded document's bytes.

The Wallet write happens synchronously in the upload request (unchanged); a
worker process picks up the analysis later, and needs the same bytes to do
it. This is the only place raw document content is held durably -- always
encrypted at rest, and deleted as soon as the worker that reads it reaches a
terminal state (see worker.py). Never queried for content outside that one
reader.
"""

from cryptography.fernet import Fernet
from sqlalchemy import text

from app.services.documents import StoredFile


def stage_upload(engine, run_id: str, file: StoredFile, key: bytes) -> None:
    ciphertext = Fernet(key).encrypt(file.data)
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO dpr_upload_staging
                    (run_id, ciphertext, filename, content_type)
                VALUES (:run_id, :ciphertext, :filename, :content_type)
                ON CONFLICT (run_id) DO UPDATE
                  SET ciphertext = EXCLUDED.ciphertext,
                      filename = EXCLUDED.filename,
                      content_type = EXCLUDED.content_type,
                      created_at = now()
            """),
            {
                "run_id": run_id,
                "ciphertext": ciphertext,
                "filename": file.name,
                "content_type": file.content_type,
            },
        )


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
