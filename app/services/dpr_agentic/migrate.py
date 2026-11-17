"""Applies Core LAS's Postgres migrations (migrations/*.sql).

Every migration here is additive and idempotent (CREATE TABLE/INDEX IF NOT
EXISTS), so calling this on every startup is safe even with several replicas
starting concurrently. It does not touch Wallet or any other schema.

Local runs apply them on startup (DB_MIGRATE_ON_STARTUP=true). UAT and prod
connect as a DML-only role that cannot run them, so they turn that off and
run `python scripts/migrate.py` as the schema's owner on each deploy;
`core_engine` then only checks the schema is there.
"""

import os
from pathlib import Path

from sqlalchemy import text

from app.services.dpr_agentic.jobs import engine_for

# The repo's migrations/ for a source checkout; an installed package (the
# Docker image) cannot find it from __file__, so the image sets
# LAS_MIGRATIONS_DIR.
MIGRATIONS_DIR = Path(
    os.environ.get("LAS_MIGRATIONS_DIR")
    or Path(__file__).resolve().parents[3] / "migrations"
)

# A table the first migration creates: present = migrations have been run.
_MARKER_TABLE = "dpr_analysis_runs"


class SchemaNotReady(RuntimeError):
    """The configured schema is missing, or its migrations were never run."""


def _check_current_schema(conn, schema: str | None) -> None:
    # current_schema() is the first *existing* schema on search_path, so a
    # missing schema shows up as "public" here rather than as an error.
    if schema is None:
        return
    current = conn.execute(text("SELECT current_schema()")).scalar()
    if current != schema:
        raise SchemaNotReady(
            f"database schema {schema!r} is not the connection's current schema "
            f"(got {current!r}); create it first (deploy/db/setup.sql)"
        )


def apply_migrations(engine, schema: str | None = None) -> None:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        raise SchemaNotReady(f"no migrations found in {MIGRATIONS_DIR}")
    with engine.begin() as conn:
        _check_current_schema(conn, schema)
        for migration in files:
            conn.execute(text(migration.read_text()))


def check_schema(engine, schema: str | None = None) -> None:
    """Fail clearly when migrations are not run here, instead of on first use."""
    with engine.connect() as conn:
        _check_current_schema(conn, schema)
        marker = conn.execute(text(f"SELECT to_regclass('{_MARKER_TABLE}')")).scalar()
        if marker is None:
            raise SchemaNotReady(
                f"table {_MARKER_TABLE} is missing: run `python scripts/migrate.py` "
                "as the schema owner"
            )


def core_engine(settings, *, migrate: bool | None = None):
    """The engine on dpr_database_url, migrated or checked per the settings."""
    url = settings.dpr_database_url.get_secret_value()
    engine = engine_for(url, settings.db_schema)
    if settings.db_migrate_on_startup if migrate is None else migrate:
        apply_migrations(engine, settings.db_schema)
    else:
        check_schema(engine, settings.db_schema)
    return engine
