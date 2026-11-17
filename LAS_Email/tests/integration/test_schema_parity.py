"""Migration == docs/schema.sql (catalog diff), models agree with the DB, MV refreshes concurrently."""

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import inspect, make_url, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from las.common.db import schema_connect_args
from las.common.models import Base
from las.common.sqlsplit import split_sql
from tests.conftest import _run_migrations, create_test_schema

CATALOG_QUERIES = {
    "columns": """
        SELECT table_name, column_name, data_type, udt_name, is_nullable,
               coalesce(column_default, ''), coalesce(character_maximum_length, 0),
               coalesce(numeric_precision, 0), coalesce(numeric_scale, 0)
        FROM information_schema.columns WHERE table_schema = current_schema()
          AND table_name <> 'alembic_version'""",
    "constraints": """
        SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid)
        FROM pg_constraint WHERE connamespace = current_schema()::regnamespace
          AND conrelid::regclass::text <> 'alembic_version'""",
    "indexes": """
        SELECT tablename, indexname, indexdef FROM pg_indexes
        WHERE schemaname = current_schema() AND tablename <> 'alembic_version'""",
    "triggers": """
        SELECT tgrelid::regclass::text, tgname, pg_get_triggerdef(oid)
        FROM pg_trigger WHERE NOT tgisinternal""",
    "views": """
        SELECT viewname, pg_get_viewdef(viewname::regclass, true) FROM pg_views WHERE schemaname = current_schema()""",
    "matviews": """
        SELECT matviewname, pg_get_viewdef(matviewname::regclass, true) FROM pg_matviews WHERE schemaname = current_schema()""",
    "sequences": """
        SELECT sequence_name, start_value, increment FROM information_schema.sequences
        WHERE sequence_schema = current_schema()""",
    "functions": """
        SELECT proname, pg_get_functiondef(oid) FROM pg_proc WHERE pronamespace = current_schema()::regnamespace
          AND prokind = 'f'
          AND proname IN ('audit_log_immutable', 'refresh_ai_usage_daily')""",
    "extensions": "SELECT extname FROM pg_extension",
}


async def _catalog(url: str) -> dict[str, set[tuple[Any, ...]]]:
    eng = create_async_engine(url, connect_args=schema_connect_args())
    try:
        async with eng.connect() as c:
            return {
                k: {tuple(r) for r in (await c.execute(text(q))).all()}
                for k, q in CATALOG_QUERIES.items()
            }
    finally:
        await eng.dispose()


async def test_migration_matches_schema_sql(admin_url: str) -> None:
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    names = [f"las_parity_mig_{uuid.uuid4().hex[:8]}", f"las_parity_sql_{uuid.uuid4().hex[:8]}"]
    urls = [
        make_url(admin_url).set(database=n).render_as_string(hide_password=False) for n in names
    ]
    try:
        async with admin.connect() as c:
            for n in names:
                await c.execute(text(f'CREATE DATABASE "{n}"'))
        for u in urls:
            e = create_async_engine(u, isolation_level="AUTOCOMMIT")
            async with e.connect() as c:
                await create_test_schema(c)
            await e.dispose()
        await asyncio.to_thread(_run_migrations, urls[0])
        eng = create_async_engine(urls[1], connect_args=schema_connect_args())
        async with eng.begin() as c:
            schema = await asyncio.to_thread(Path("docs/schema.sql").read_text)
            for stmt in split_sql(schema):
                await c.execute(text(stmt.replace(":", "\\:")))
        await eng.dispose()
        migrated, reference = await _catalog(urls[0]), await _catalog(urls[1])
        for key in CATALOG_QUERIES:
            assert migrated[key] == reference[key], (
                key,
                sorted(migrated[key] - reference[key])[:5],
                sorted(reference[key] - migrated[key])[:5],
            )
    finally:
        async with admin.connect() as c:
            for n in names:
                await c.execute(text(f'DROP DATABASE IF EXISTS "{n}" WITH (FORCE)'))
        await admin.dispose()


def _db_columns(sync_conn: Any) -> dict[str, dict[str, bool]]:
    insp = inspect(sync_conn)
    return {
        t: {c["name"]: c["nullable"] for c in insp.get_columns(t)} for t in insp.get_table_names()
    }


async def test_models_match_database(engine: AsyncEngine) -> None:
    async with engine.connect() as c:
        db_cols = await c.run_sync(_db_columns)
    for table in Base.metadata.tables.values():
        assert table.name in db_cols, table.name
        model_cols = {col.name: bool(col.nullable) for col in table.columns}
        assert model_cols == db_cols[table.name], table.name
    missing = set(db_cols) - set(Base.metadata.tables) - {"alembic_version"}
    assert not missing


async def test_refresh_materialized_view_concurrently(engine: AsyncEngine) -> None:
    async with engine.connect() as c:
        conn: AsyncConnection = await c.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text("REFRESH MATERIALIZED VIEW mv_ai_usage_daily"))
        await conn.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_ai_usage_daily"))


@pytest.mark.commits
async def test_mv_rollup_counts_usage(engine: AsyncEngine, db: Any) -> None:
    async with engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO ai_usage (provider, model, purpose, prompt_template, prompt_version, status, "
                "input_tokens, output_tokens, latency_ms) VALUES "
                "('google_gemini','m','INTENT_EXTRACTION','intent_extract','v1','SUCCESS',100,20,300),"
                "('google_gemini','m','INTENT_EXTRACTION','intent_extract','v1','SUCCESS',50,10,500)"
            )
        )
    async with engine.connect() as c:
        conn = await c.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_ai_usage_daily"))
        row = (
            await conn.execute(
                text("SELECT calls, input_tokens, bd_key FROM mv_ai_usage_daily WHERE model='m'")
            )
        ).one()
    assert (row.calls, row.input_tokens, row.bd_key) == (2, 150, 0)
