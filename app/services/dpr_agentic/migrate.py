"""Applies the DPR-agentic Postgres migrations.

Every migration here is additive and idempotent (CREATE TABLE/INDEX IF NOT
EXISTS), so calling this on every app startup is safe even with several
replicas starting concurrently. It does not touch Wallet or any other schema.
"""

from pathlib import Path

from sqlalchemy import text

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


def apply_migrations(engine) -> None:
    with engine.begin() as conn:
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute(text(migration.read_text()))
