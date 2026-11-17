"""`make db-check`: server version, required extensions and schema on DATABASE_URL."""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from las.common.db import schema_connect_args
from las.common.settings import get_settings

MIN_MAJOR = 14  # local dev; production (Cloud SQL) runs 16


async def _check() -> int:
    url = get_settings().database_url
    engine = create_async_engine(url, connect_args=schema_connect_args())
    ok = True
    try:
        async with engine.connect() as c:
            version = int((await c.execute(text("SHOW server_version_num"))).scalar_one())
            major = version // 10000
            print(f"server version: {major} ({version})")
            if major < MIN_MAJOR:
                print(f"FAIL: PostgreSQL >= {MIN_MAJOR} required")
                ok = False
            elif major < 16:
                print(
                    "note: production target is PostgreSQL 16; only 14-compatible features are used"
                )
            # Checked, not created: on a shared database the app role cannot create
            # extensions; the DBA does once (Core LAS deploy/db/setup.sql).
            for ext in ("pgcrypto", "citext"):
                found = (
                    await c.execute(
                        text("SELECT 1 FROM pg_extension WHERE extname = :e"), {"e": ext}
                    )
                ).scalar()
                if found:
                    print(f"extension {ext}: ok")
                else:
                    print(f"FAIL: extension {ext} missing; ask the DBA to CREATE EXTENSION {ext}")
                    ok = False
            schema = get_settings().db_schema
            current = (await c.execute(text("SELECT current_schema()"))).scalar()
            if current == schema:
                print(f"schema {schema}: ok")
            else:
                print(f"FAIL: DB_SCHEMA={schema} but the current schema is {current}")
                ok = False
    finally:
        await engine.dispose()
    return 0 if ok else 1


def main() -> None:
    sys.exit(asyncio.run(_check()))


if __name__ == "__main__":
    main()
