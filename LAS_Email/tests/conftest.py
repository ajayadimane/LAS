"""Test fixtures.

A session fixture connects with TEST_DATABASE_ADMIN_URL, creates ``las_test_<random>``, runs the
Alembic migrations into it and drops it at the end (also on failure). With DB_SCHEMA set (e.g.
``email``, the shared-database layout) the schema is created first and everything runs inside it. Tests get a session factory
bound to one connection inside a transaction that is rolled back (savepoints absorb the app's
commits). Tests marked ``@pytest.mark.commits`` get real commits and truncated tables instead.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from las.common.db import SessionFactory, schema_connect_args
from las.common.deps import Deps, build_fake_deps
from las.common.settings import Settings, get_settings, to_async_url
from las.scripts.seed import seed_reference_data

# Tables that commit-tests may dirty. Reference data and the append-only audit_log are kept.
TRANSACTIONAL_TABLES = [
    "core_intake_handoffs",
    "mail_actions",
    "ai_usage",
    "email_attachments",
    "inbound_emails",
    "mailbox_checkpoints",
]


def _admin_url() -> str:
    url = os.environ.get("TEST_DATABASE_ADMIN_URL") or get_settings().test_database_admin_url
    if not url:
        pytest.exit("TEST_DATABASE_ADMIN_URL is not set (see .env.example)", returncode=2)
    return to_async_url(url)


async def create_test_schema(conn) -> None:
    """The DB_SCHEMA schema in a throwaway database (nothing for public)."""
    schema = get_settings().db_schema
    if schema != "public":
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))


def _run_migrations(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def admin_url() -> str:
    return _admin_url()


@pytest.fixture(scope="session")
async def test_db_url(admin_url: str) -> AsyncIterator[str]:
    name = f"las_test_{uuid.uuid4().hex[:10]}"
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as c:
        await c.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    try:
        test_db = create_async_engine(url, isolation_level="AUTOCOMMIT")
        async with test_db.connect() as c:
            await create_test_schema(c)
        await test_db.dispose()
        await asyncio.to_thread(_run_migrations, url)
        yield url
    finally:
        async with admin.connect() as c:
            await c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


@pytest.fixture(scope="session")
async def engine(test_db_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(
        test_db_url, pool_size=10, max_overflow=10, connect_args=schema_connect_args()
    )
    async with eng.begin() as conn, AsyncSession(bind=conn) as s:
        await seed_reference_data(s, test_bds=True)
        await s.commit()
    yield eng
    await eng.dispose()


@pytest.fixture
def settings(test_db_url: str) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        env="test",
        database_url=test_db_url,
        av_scanner="fake",
        document_parser="fake",
        auth_dns_fallback=False,
        core_intake_url="https://core.test/las-api/internal/v1/intake/events",
        core_intake_api_key="test-core-intake-key",
    )


@pytest.fixture
async def db(engine: AsyncEngine, request: pytest.FixtureRequest) -> AsyncIterator[SessionFactory]:
    if request.node.get_closest_marker("commits"):
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            yield factory
        finally:
            async with engine.begin() as conn:
                await conn.execute(
                    text(f"TRUNCATE {', '.join(TRANSACTIONAL_TABLES)} RESTART IDENTITY CASCADE")
                )
                await conn.execute(
                    text(
                        "DELETE FROM bd_partners WHERE ibkart_bd_code NOT IN ('BD-TEST-001','BD-TEST-002')"
                    )
                )
                await conn.execute(
                    text("UPDATE bd_partners SET status='ACTIVE', daily_request_cap=50")
                )
        return
    async with engine.connect() as conn:
        trans = await conn.begin()
        factory = async_sessionmaker(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield factory
        finally:
            await trans.rollback()


@pytest.fixture
def deps(settings: Settings, db: SessionFactory) -> Deps:
    return build_fake_deps(settings, db)


@pytest.fixture(autouse=True)
def _quiet_logs() -> Iterator[None]:
    from las.common.logging import configure_logging

    configure_logging("WARNING")
    yield
