"""What a customer still owes for services paid by Pay later
(migrations/015_pay_later_dues.sql).

Pay later: the pool pays at once and the BD stands guarantor, but the
customer still owes it. Until the BD confirms receipt, the service's report
is locked for the customer and the BD (app/services/dpr_agentic/
report_service.py) and its PDF is not written to the wallet
(app/services/intake/processor.py:_sync_reports).

A Pay later service with no row counts as due -- fail closed. Synchronous
SQLAlchemy; async callers use asyncio.to_thread.
"""

from dataclasses import dataclass

from sqlalchemy import text


@dataclass(frozen=True)
class Due:
    service_code: str
    amount: int | None
    status: str


def record_dues(
    engine,
    person_id: str,
    amounts: dict[str, int],
    transfer_txn: str | None,
) -> None:
    """A Pay later payment went through: these services are now owed. A
    service already settled stays settled."""
    with engine.begin() as conn:
        for code, amount in amounts.items():
            conn.execute(
                text("""
                    INSERT INTO pay_later_dues
                        (person_id, service_code, amount, transfer_txn)
                    VALUES (:p, :c, :a, :t)
                    ON CONFLICT (person_id, service_code) DO UPDATE
                      SET amount = EXCLUDED.amount, transfer_txn = EXCLUDED.transfer_txn
                      WHERE pay_later_dues.status = 'DUE'
                """),
                {"p": person_id, "c": code, "a": amount, "t": transfer_txn},
            )


def is_settled(engine, person_id: str, code: str) -> bool:
    """True only once the BD has confirmed the customer paid."""
    with engine.connect() as conn:
        return (
            conn.execute(
                text(
                    "SELECT 1 FROM pay_later_dues WHERE person_id = :p "
                    "AND service_code = :c AND status = 'SETTLED'"
                ),
                {"p": person_id, "c": code},
            ).first()
            is not None
        )


def dues_for(engine, person_id: str) -> list[Due]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT service_code, amount, status FROM pay_later_dues "
                "WHERE person_id = :p ORDER BY service_code"
            ),
            {"p": person_id},
        ).all()
    return [Due(r[0], r[1], r[2]) for r in rows]


def settle(engine, person_id: str, codes: list[str], settled_by: str) -> list[str]:
    """Mark these services paid by the customer (creating the row for a
    Pay later service recorded before dues existed). Returns the codes that
    changed from due to settled."""
    changed: list[str] = []
    with engine.begin() as conn:
        for code in codes:
            row = conn.execute(
                text("""
                    INSERT INTO pay_later_dues (person_id, service_code, status,
                        settled_at, settled_by)
                    VALUES (:p, :c, 'SETTLED', now(), :by)
                    ON CONFLICT (person_id, service_code) DO UPDATE
                      SET status = 'SETTLED', settled_at = now(), settled_by = :by
                      WHERE pay_later_dues.status = 'DUE'
                    RETURNING service_code
                """),
                {"p": person_id, "c": code, "by": settled_by},
            ).first()
            if row is not None:
                changed.append(code)
    return changed


def requeue_report_sync(engine, person_id: str, codes: list[str]) -> int:
    """After the customer paid: write the held-back report PDF to the wallet
    (and email the BD) on the intake worker's next pass."""
    refs = [f"wallet-person-custom-field:{person_id}:{c}" for c in codes]
    with engine.begin() as conn:
        return conn.execute(
            text("""
                UPDATE dpr_report_wallet_sync
                SET status = 'PENDING', attempts = 0, next_attempt_at = now(),
                    updated_at = now()
                WHERE source_reference = ANY(:refs) AND outcome = 'LOCKED_DUE'
            """),
            {"refs": refs},
        ).rowcount


# -- the async view the API and the intake worker share --------------------------


@dataclass(frozen=True)
class Outstanding:
    code: str
    name: str
    amount: int


async def outstanding(
    enrolment, engine, person_id: str, broker_id, ctx
) -> list[Outstanding]:
    """Pay later services this customer has not paid for yet. `broker_id`
    None = the customer themselves (person_id is the token's user)."""
    import asyncio

    view = await enrolment.customer_services(person_id, broker_id, ctx)
    settings = enrolment._settings  # noqa: SLF001
    recorded = (
        {
            d.service_code: d
            for d in await asyncio.to_thread(dues_for, engine, person_id)
        }
        if engine is not None
        else {}
    )
    out: list[Outstanding] = []
    for s in view.services:
        if not (
            s.opted
            and (s.payment_mode or "").casefold()
            == settings.payment_mode_pay_later.casefold()
        ):
            continue
        row = recorded.get(s.code)
        if row is not None and row.status == "SETTLED":
            continue
        amount = row.amount if row is not None and row.amount is not None else s.fee
        out.append(Outstanding(s.code, s.name, amount))
    return out
