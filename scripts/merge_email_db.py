"""One-time: move a local setup from two databases to one (schemas core + email).

Before (development): Core's tables in database LAS, LAS_Email's in database
LAS_Email, both in schema public. After: one database, Core in schema core and
LAS_Email in schema email -- the layout UAT and prod start with. UAT and prod
never run this: they start empty. Runbook: deploy/db/README.md.

  1. pg_dump -Fc both databases; stop Core, the DPR worker and LAS_Email.
  2. psql -d LAS -f deploy/db/setup.sql   (roles, schemas core + email, grants)
  3. python scripts/merge_email_db.py --target-url <LAS superuser> --move-core
  4. in LAS_Email/: DB_SCHEMA=email MIGRATION_DATABASE_URL=<email_owner> make migrate
  5. python scripts/merge_email_db.py --target-url <LAS superuser> \
         --source-url <LAS_Email URL> --copy-email
  6. psql -d LAS -f deploy/db/setup.sql   (again: grants on moved/new objects)
  7. Point both .env files at the app roles + DB_SCHEMA, start, check.

--move-core: ALTER ... SET SCHEMA core and OWNER TO core_owner for Core's
tables and trigger functions; data, indexes, constraints, triggers and owned
sequences move with them.
--copy-email: copies every LAS_Email table (FK order, audit_log in audit_id
order) into the migrated email schema, sets the sequences, refreshes
mv_ai_usage_daily and compares a checksum of every table with the source.
Nothing is written to the source. Each run is one transaction: a failure
changes nothing. --dry-run does everything, then rolls back.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable

import psycopg
from psycopg import sql

CORE_TABLES = [
    "dpr_analysis_runs",
    "dpr_analysis_branch_status",
    "dpr_report_artifacts",
    "dpr_upload_staging",
    "dpr_ocr_cache",
    "dpr_grounding_cache",
    "dpr_report_wallet_sync",
    "intake_events",
    "intake_cases",
    "intake_case_messages",
    "intake_attachments",
    "pay_later_dues",
]
CORE_FUNCTIONS = ["dpr_report_artifacts_immutable", "dpr_cam_snapshot_immutable"]
# Live data in the target means this is not a fresh schema: refuse rather than mix.
EMAIL_LIVE_TABLES = ["inbound_emails", "audit_log"]
MATVIEW = "mv_ai_usage_daily"


def _libpq(url: str) -> str:
    """SQLAlchemy-style URLs (postgresql+psycopg://, +asyncpg) as libpq URLs."""
    scheme, sep, rest = url.partition("://")
    return f"{scheme.split('+')[0]}{sep}{rest}"


def _tables(conn, schema: str) -> list[str]:
    rows = conn.execute(
        "SELECT c.relname FROM pg_class c WHERE c.relnamespace = %s::regnamespace "
        "AND c.relkind = 'r' AND c.relname <> 'alembic_version' ORDER BY 1",
        (schema,),
    ).fetchall()
    return [r[0] for r in rows]


def _fk_order(conn, schema: str, tables: Iterable[str]) -> list[str]:
    """Parents before children (self-references ignored)."""
    tables = list(tables)
    deps = {t: set() for t in tables}
    for child, parent in conn.execute(
        "SELECT conrelid::regclass::text, confrelid::regclass::text FROM pg_constraint "
        "WHERE contype = 'f' AND connamespace = %s::regnamespace",
        (schema,),
    ):
        child, parent = child.split(".")[-1], parent.split(".")[-1]
        if child in deps and parent in deps and child != parent:
            deps[child].add(parent)
    ordered: list[str] = []
    while deps:
        ready = sorted(t for t, d in deps.items() if not d - set(ordered))
        if not ready:
            raise SystemExit(f"FK cycle among {sorted(deps)}")
        for t in ready:
            ordered.append(t)
            del deps[t]
    return ordered


def _columns(conn, schema: str, table: str) -> list[tuple[str, bool]]:
    return conn.execute(
        "SELECT column_name, is_nullable = 'NO' AND column_default IS NULL "
        "FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
        "AND is_generated = 'NEVER' ORDER BY ordinal_position",
        (schema, table),
    ).fetchall()


def _checksum(conn, schema: str, table: str, cols: list[str]) -> tuple[int, str]:
    """Row count and md5 over every row's text form, order-independent."""
    row = sql.SQL("ROW({})::text").format(sql.SQL(", ").join(map(sql.Identifier, cols)))
    q = sql.SQL(
        "SELECT count(*), coalesce(md5(string_agg(r, E'\\n' ORDER BY r)), '') "
        "FROM (SELECT {} AS r FROM {}.{}) s"
    ).format(row, sql.Identifier(schema), sql.Identifier(table))
    return tuple(conn.execute(q).fetchone())


def move_core(target, role_prefix: str) -> None:
    owner = f"{role_prefix}core_owner"
    if not target.execute(
        "SELECT 1 FROM pg_namespace WHERE nspname = 'core'"
    ).fetchone():
        raise SystemExit("schema core is missing: run deploy/db/setup.sql first")
    present = set(_tables(target, "public"))
    for t in CORE_TABLES:
        if t not in present:
            print(f"  skip {t}: not in public (already moved?)")
            continue
        target.execute(
            sql.SQL("ALTER TABLE public.{} SET SCHEMA core").format(sql.Identifier(t))
        )
        target.execute(
            sql.SQL("ALTER TABLE core.{} OWNER TO {}").format(
                sql.Identifier(t), sql.Identifier(owner)
            )
        )
        print(f"  moved {t} -> core (owner {owner})")
    for f in CORE_FUNCTIONS:
        if target.execute(
            "SELECT 1 FROM pg_proc WHERE proname = %s "
            "AND pronamespace = 'public'::regnamespace",
            (f,),
        ).fetchone():
            target.execute(
                sql.SQL("ALTER FUNCTION public.{}() SET SCHEMA core").format(
                    sql.Identifier(f)
                )
            )
            target.execute(
                sql.SQL("ALTER FUNCTION core.{}() OWNER TO {}").format(
                    sql.Identifier(f), sql.Identifier(owner)
                )
            )
            print(f"  moved function {f}() -> core")
    left = set(_tables(target, "public")) & set(CORE_TABLES)
    if left:
        raise SystemExit(f"still in public: {sorted(left)}")


def copy_email(source, target) -> None:
    src_tables = _tables(source, "public")
    dst_tables = _tables(target, "email")
    if not dst_tables:
        raise SystemExit(
            "schema email has no tables: run LAS_Email's `make migrate` first"
        )
    src_ver = source.execute(
        "SELECT version_num FROM public.alembic_version"
    ).fetchone()
    dst_ver = target.execute("SELECT version_num FROM email.alembic_version").fetchone()
    print(f"  alembic: source {src_ver[0]}, target {dst_ver[0]}")
    if missing := sorted(set(src_tables) - set(dst_tables)):
        raise SystemExit(
            f"tables in the source but not in email: {missing} (data would be lost)"
        )
    for t in EMAIL_LIVE_TABLES:
        n = target.execute(
            sql.SQL("SELECT count(*) FROM email.{}").format(sql.Identifier(t))
        ).fetchone()[0]
        if n:
            raise SystemExit(
                f"email.{t} already has {n} rows: not a fresh schema, refusing"
            )

    order = _fk_order(target, "email", src_tables)
    # Reference rows seeded by migrations (e.g. 0008's service catalog) are
    # replaced by the source's own copy; children first.
    for t in reversed(order):
        if t not in EMAIL_LIVE_TABLES:
            target.execute(sql.SQL("DELETE FROM email.{}").format(sql.Identifier(t)))

    for t in order:
        src_cols = _columns(source, "public", t)
        dst_cols = _columns(target, "email", t)
        src_names = [c for c, _ in src_cols]
        if extra := [c for c in src_names if c not in {c for c, _ in dst_cols}]:
            raise SystemExit(f"{t}: source columns missing in email: {extra}")
        if required := [c for c, req in dst_cols if req and c not in src_names]:
            raise SystemExit(
                f"{t}: required email columns missing in the source: {required}"
            )
        cols = sql.SQL(", ").join(map(sql.Identifier, src_names))
        order_by = "audit_id" if t == "audit_log" else "1"
        copy_out = sql.SQL(
            "COPY (SELECT {} FROM public.{} ORDER BY {}) TO STDOUT"
        ).format(cols, sql.Identifier(t), sql.SQL(order_by))
        copy_in = sql.SQL("COPY email.{} ({}) FROM STDIN").format(
            sql.Identifier(t), cols
        )
        with (
            source.cursor().copy(copy_out) as out,
            target.cursor().copy(copy_in) as into,
        ):
            for block in out:
                into.write(block)

        for (col,) in target.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'email' AND table_name = %s "
            "AND column_default LIKE 'nextval(%%'",
            (t,),
        ).fetchall():
            target.execute(
                sql.SQL(
                    "SELECT setval(pg_get_serial_sequence({qual}, {col}), "
                    "coalesce((SELECT max({ident}) FROM email.{tbl}), 0) + 1, false)"
                ).format(
                    qual=sql.Literal(f"email.{t}"),
                    col=sql.Literal(col),
                    ident=sql.Identifier(col),
                    tbl=sql.Identifier(t),
                )
            )

        src_sum = _checksum(source, "public", t, src_names)
        dst_sum = _checksum(target, "email", t, src_names)
        status = "ok" if src_sum == dst_sum else "MISMATCH"
        print(f"  {t}: {src_sum[0]} rows, checksum {status}")
        if src_sum != dst_sum:
            raise SystemExit(
                f"{t}: copied data differs from the source; nothing committed"
            )

    target.execute(
        sql.SQL("REFRESH MATERIALIZED VIEW email.{}").format(sql.Identifier(MATVIEW))
    )
    print(f"  refreshed {MATVIEW}")


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--target-url", required=True, help="superuser URL of the single database"
    )
    p.add_argument(
        "--source-url", help="URL of the old LAS_Email database (--copy-email)"
    )
    p.add_argument("--move-core", action="store_true")
    p.add_argument("--copy-email", action="store_true")
    p.add_argument(
        "--role-prefix", default="", help="as given to setup.sql (e.g. uat_)"
    )
    p.add_argument(
        "--dry-run", action="store_true", help="do everything, then roll back"
    )
    a = p.parse_args()
    if not (a.move_core or a.copy_email):
        p.error("choose --move-core and/or --copy-email")
    if a.copy_email and not a.source_url:
        p.error("--copy-email needs --source-url")

    with psycopg.connect(_libpq(a.target_url)) as target:
        if a.move_core:
            print("move Core tables to schema core")
            move_core(target, a.role_prefix)
        if a.copy_email:
            print("copy LAS_Email into schema email")
            with psycopg.connect(_libpq(a.source_url)) as source:
                source.read_only = True
                copy_email(source, target)
        if a.dry_run:
            target.rollback()
            print("dry run: rolled back, nothing changed")
        else:
            target.commit()
            print("committed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
