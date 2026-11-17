"""Apply Core LAS's migrations (migrations/*.sql) as the schema owner.

The deploy step for UAT and prod, where the app connects as a DML-only role
(DB_MIGRATE_ON_STARTUP=false) that cannot create or alter tables:

    DB_MIGRATION_URL=postgresql+psycopg://core_owner:...@host/las \\
    DB_SCHEMA=core python scripts/migrate.py

Uses DB_MIGRATION_URL, else DPR_DATABASE_URL; the schema is DB_SCHEMA.
Idempotent: safe to run on every deploy. See deploy/db/README.md.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.services.dpr_agentic.jobs import engine_for  # noqa: E402
from app.services.dpr_agentic.migrate import (  # noqa: E402
    MIGRATIONS_DIR,
    apply_migrations,
    check_schema,
)


def main() -> int:
    settings = get_settings()
    url = settings.db_migration_url or settings.dpr_database_url
    if url is None:
        sys.exit("migrate: set DB_MIGRATION_URL or DPR_DATABASE_URL")
    engine = engine_for(url.get_secret_value(), settings.db_schema)
    try:
        apply_migrations(engine, settings.db_schema)
        check_schema(engine, settings.db_schema)
    finally:
        engine.dispose()
    count = len(list(MIGRATIONS_DIR.glob("*.sql")))
    print(f"migrate: applied {count} migrations to schema {settings.db_schema!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
