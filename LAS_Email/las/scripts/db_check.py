"""`make db-check`: server version and required extensions on DATABASE_URL."""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from las.common.settings import get_settings

MIN_MAJOR = 14  # local dev; production (Cloud SQL) runs 16


async def _check() -> int:
    url = get_settings().database_url
    engine = create_async_engine(url)
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
            for ext in ("pgcrypto", "citext"):
                try:
                    async with c.begin_nested():  # savepoint: one failure doesn't abort the rest
                        await c.execute(text(f"CREATE EXTENSION IF NOT EXISTS {ext}"))
                    print(f"extension {ext}: ok")
                except Exception as e:  # noqa: BLE001
                    print(f"FAIL: cannot create extension {ext}: {e}")
                    ok = False
            await c.rollback()  # check only; migrations create them for real
    finally:
        await engine.dispose()
    return 0 if ok else 1


def main() -> None:
    sys.exit(asyncio.run(_check()))


if __name__ == "__main__":
    main()
