"""Alembic environment (async engine).

URL: ``-x url=...`` > config sqlalchemy.url > MIGRATION_DATABASE_URL > DATABASE_URL.
Objects and ``alembic_version`` go in DB_SCHEMA (search_path ``<schema>, public``).
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from las.common.db import schema_connect_args
from las.common.models import Base
from las.common.settings import get_settings, to_async_url

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    x = context.get_x_argument(as_dictionary=True)
    s = get_settings()
    url = (
        x.get("url")
        or config.get_main_option("sqlalchemy.url")
        or s.migration_database_url
        or s.database_url
    )
    return to_async_url(url)


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        version_table_schema=get_settings().db_schema,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection: Connection) -> None:
    schema = get_settings().db_schema
    # current_schema() is the first *existing* schema on search_path: a missing one shows up as
    # "public" here, and every table would silently be created there instead.
    current = connection.exec_driver_sql("SELECT current_schema()").scalar()
    # End the transaction that query auto-began: Alembic only commits a transaction it began
    # itself, so leaving this open would roll every migration back when the connection closes.
    connection.rollback()
    if current != schema:
        raise RuntimeError(
            f"DB_SCHEMA={schema!r} but the connection's current schema is {current!r}: "
            "create the schema first (Core LAS deploy/db/setup.sql)"
        )
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=get_settings().db_schema,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url(), connect_args=schema_connect_args())
    async with engine.connect() as conn:
        await conn.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
