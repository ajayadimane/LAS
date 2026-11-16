"""Async SQLAlchemy engine/session management.

Business code receives an ``async_sessionmaker`` (``SessionFactory``) and owns its transaction
boundaries. Tests bind the factory to a single connection with savepoints; commit-tests bind it to
a real engine.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TypeAlias

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from las.common.settings import get_settings

SessionFactory: TypeAlias = async_sessionmaker[AsyncSession]

_engine: AsyncEngine | None = None
_factory: SessionFactory | None = None


def make_engine(url: str | None = None, *, pool_size: int | None = None) -> AsyncEngine:
    s = get_settings()
    return create_async_engine(
        url or s.database_url,
        pool_size=pool_size or s.db_pool_size,
        pool_pre_ping=True,
        echo=s.db_echo,
    )


def make_session_factory(engine: AsyncEngine) -> SessionFactory:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine


def get_session_factory() -> SessionFactory:
    global _factory
    if _factory is None:
        _factory = make_session_factory(get_engine())
    return _factory


async def dispose_engine() -> None:
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _factory = None


@asynccontextmanager
async def session_scope(factory: SessionFactory) -> AsyncIterator[AsyncSession]:
    """One unit of work: commit on success, roll back on error."""
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def advisory_xact_lock(session: AsyncSession, key: str) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})
