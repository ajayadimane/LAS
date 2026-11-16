import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from las.common.audit import verify_chain, write_audit
from las.common.db import SessionFactory


@pytest.mark.commits
async def test_concurrent_writers_produce_one_unbroken_chain(db: SessionFactory) -> None:
    async def writer(n: int) -> None:
        for i in range(10):
            async with db() as s:
                await write_audit(
                    s,
                    actor_type="SYSTEM",
                    action="TEST",
                    entity_type="t",
                    entity_id=f"{n}-{i}",
                    after={"n": n, "i": i, "amount": "12.50"},
                )
                await asyncio.sleep(0)  # widen the race window between read and commit
                await s.commit()

    await asyncio.gather(*(writer(n) for n in range(8)))
    async with db() as s:
        report = await verify_chain(s)
        prev_links = (
            await s.execute(
                text(
                    "SELECT count(*) - count(DISTINCT prev_hash) FROM audit_log WHERE prev_hash IS NOT NULL"
                )
            )
        ).scalar_one()
    assert report.ok, report
    assert report.rows_checked >= 80
    assert prev_links == 0  # no two rows chain onto the same predecessor (no fork)


@pytest.mark.commits
async def test_truncate_update_delete_are_blocked(db: SessionFactory) -> None:
    async with db() as s:
        await write_audit(s, actor_type="SYSTEM", action="TEST", entity_type="t", entity_id="x")
        await s.commit()
    for stmt in ("TRUNCATE audit_log", "UPDATE audit_log SET action='X'", "DELETE FROM audit_log"):
        async with db() as s:
            with pytest.raises(DBAPIError, match="append-only"):
                await s.execute(text(stmt))
            await s.rollback()


async def test_verify_detects_tampering(db: SessionFactory) -> None:
    async with db() as s:
        for i in range(3):
            await write_audit(
                s,
                actor_type="SYSTEM",
                action="TEST",
                entity_type="t",
                entity_id=str(i),
                after={"i": i},
            )
        assert (await verify_chain(s)).ok
        # Simulate an attacker with DDL rights; rolled back with the test transaction.
        await s.execute(text("ALTER TABLE audit_log DISABLE TRIGGER trg_audit_no_update"))
        await s.execute(
            text(
                "UPDATE audit_log SET after_state = '{\"i\": 99}' WHERE audit_id = (SELECT max(audit_id) FROM audit_log)"
            )
        )
        report = await verify_chain(s)
        assert not report.ok and report.reason == "row_hash mismatch"
