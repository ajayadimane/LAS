"""Copy the local LAS database (schemas core + email, with data) to another server.

The target is whatever the .env files now point at: DPR_DATABASE_URL and
DB_MIGRATION_URL in .env, DATABASE_URL and MIGRATION_DATABASE_URL in
LAS_Email/.env (one host, port and database). The source is the same database
name on --source-host (default the local server), with the same role passwords.

    TARGET_ADMIN_URL=postgresql://postgres:<pw>@<target-host>:5432/postgres \\
        python scripts/copy_db_to_server.py [--check]

Stop Core LAS, its DPR worker and LAS_Email first, so nothing is written mid-copy.
  1. Target, as TARGET_ADMIN_URL: create the database if missing; run
     deploy/db/setup.sql with the role passwords from the .env files.
  2. Refuse if the target's core or email schema already holds a table.
  3. pg_dump each schema as its owner (core_owner, email_owner) from the
     source; pg_restore it as that owner into the target, one transaction per
     schema. The schema and its default privileges come from setup.sql.
  4. Run setup.sql again (grants on the restored objects).
  5. Compare every table (row count + md5 of all rows); check that no sequence
     would hand out an id already in use.

--check only connects and reports; it changes nothing. Passwords never appear
on a command line or in the output.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

import psycopg
from dotenv import dotenv_values
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
SETUP_SQL = ROOT / "deploy" / "db" / "setup.sql"
SCHEMAS = {"core": "core_owner", "email": "email_owner"}
# Restore-list entries setup.sql owns: the schemas, their ACLs and default privileges.
SKIP_ENTRY = re.compile(r"\bSCHEMA - (core|email)\b|\bACL - SCHEMA\b|\bDEFAULT ACL\b")


def libpq(url: str) -> str:
    scheme, sep, rest = url.partition("://")
    return f"{scheme.split('+')[0]}{sep}{rest}"


def parts(url: str) -> dict:
    u = urlsplit(libpq(url))
    return {
        "user": unquote(u.username or ""),
        "password": unquote(u.password or ""),
        "host": u.hostname,
        "port": u.port or 5432,
        "dbname": u.path.lstrip("/"),
    }


def with_server(url: str, host: str, port: int, dbname: str | None = None) -> str:
    u = urlsplit(libpq(url))
    auth = u.netloc.rpartition("@")[0]
    return urlunsplit(
        (
            u.scheme,
            f"{auth}@{host}:{port}",
            f"/{dbname or u.path.lstrip('/')}",
            u.query,
            "",
        )
    )


def env_for(url: str) -> dict:
    """libpq environment for the CLI tools: the password travels here, not in argv."""
    p = parts(url)
    return {
        **os.environ,
        "PGHOST": p["host"],
        "PGPORT": str(p["port"]),
        "PGUSER": p["user"],
        "PGPASSWORD": p["password"],
        "PGDATABASE": p["dbname"],
        "PGCONNECT_TIMEOUT": "10",
    }


def run(cmd: list[str], url: str, stdin: str | None = None) -> str:
    r = subprocess.run(
        cmd, env=env_for(url), input=stdin, capture_output=True, text=True
    )
    if r.returncode != 0:
        raise SystemExit(f"{cmd[0]} failed:\n{r.stderr.strip()}")
    return r.stdout


def psql_quote(v: str) -> str:
    return "'" + v.replace("\\", "\\\\").replace("'", "\\'") + "'"


def run_setup(admin_db_url: str, passwords: dict[str, str]) -> None:
    preamble = "".join(
        f"\\set {k}_password {psql_quote(v)}\n" for k, v in passwords.items()
    )
    run(
        ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-f", "-"],
        admin_db_url,
        stdin=preamble + SETUP_SQL.read_text(),
    )


def tables(conn, schema: str) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT relname FROM pg_class WHERE relnamespace = %s::regnamespace "
            "AND relkind = 'r' ORDER BY 1",
            (schema,),
        )
    ]


def checksum(conn, schema: str, table: str) -> tuple:
    cols = [
        r[0]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s "
            "AND table_name = %s ORDER BY ordinal_position",
            (schema, table),
        )
    ]
    row = sql.SQL("ROW({})::text").format(sql.SQL(", ").join(map(sql.Identifier, cols)))
    q = sql.SQL(
        "SELECT count(*), coalesce(md5(string_agg(r, E'\\n' ORDER BY r)), '') "
        "FROM (SELECT {} AS r FROM {}.{}) s"
    ).format(row, sql.Identifier(schema), sql.Identifier(table))
    return tuple(conn.execute(q).fetchone())


def stale_sequences(conn, schema: str) -> list[str]:
    """Sequences whose next value would collide with a row already in the table
    (serial and IDENTITY columns alike)."""
    stale = []
    for seq, table, col in conn.execute(
        "SELECT s.relname, d.refobjid::regclass::text, a.attname FROM pg_class s "
        "JOIN pg_depend d ON d.objid = s.oid AND d.deptype IN ('a', 'i') "
        "JOIN pg_attribute a ON a.attrelid = d.refobjid AND a.attnum = d.refobjsubid "
        "WHERE s.relkind = 'S' AND s.relnamespace = %s::regnamespace",
        (schema,),
    ).fetchall():
        last, called = conn.execute(
            sql.SQL("SELECT last_value, is_called FROM {}.{}").format(
                sql.Identifier(schema), sql.Identifier(seq)
            )
        ).fetchone()
        top = conn.execute(
            sql.SQL("SELECT coalesce(max({}), 0) FROM {}").format(
                sql.Identifier(col), sql.SQL(table)
            )
        ).fetchone()[0]
        if (last + 1 if called else last) <= top:
            stale.append(f"{seq} (next {last + 1 if called else last}, max {top})")
    return stale


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    ap.add_argument("--source-host", default="127.0.0.1")
    ap.add_argument("--source-port", type=int, default=5432)
    ap.add_argument("--check", action="store_true", help="connect and report only")
    args = ap.parse_args()

    core = dotenv_values(ROOT / ".env")
    email = dotenv_values(ROOT / "LAS_Email" / ".env")
    target = {
        "core_app": core.get("DPR_DATABASE_URL"),
        "core_owner": core.get("DB_MIGRATION_URL"),
        "email_app": email.get("DATABASE_URL"),
        "email_owner": email.get("MIGRATION_DATABASE_URL"),
    }
    missing = [k for k, v in target.items() if not v]
    if missing:
        raise SystemExit(f"missing URL for {missing} in the .env files")
    for role, url in target.items():
        if parts(url)["user"] != role:
            raise SystemExit(
                f"{role}'s URL signs in as {parts(url)['user']!r}, expected {role!r}"
            )
    servers = {
        (parts(u)["host"], parts(u)["port"], parts(u)["dbname"])
        for u in target.values()
    }
    if len(servers) != 1:
        raise SystemExit(
            f"the four URLs name different servers/databases: {sorted(servers)}"
        )
    host, port, dbname = servers.pop()
    if (host, port) == (args.source_host, args.source_port):
        raise SystemExit(
            "the .env files point at the source server itself; nothing to copy"
        )
    source = {
        role: with_server(target[role], args.source_host, args.source_port)
        for role in SCHEMAS.values()
    }
    passwords = {role: parts(url)["password"] for role, url in target.items()}

    admin = os.environ.get("TARGET_ADMIN_URL")
    if not admin:
        raise SystemExit("set TARGET_ADMIN_URL (an admin login on the target server)")
    admin = libpq(admin)
    ap_ = parts(admin)
    if (ap_["host"], ap_["port"]) != (host, port):
        raise SystemExit(
            f"TARGET_ADMIN_URL is not {host}:{port} (the .env files' server)"
        )
    admin_db = with_server(admin, host, port, dbname)

    src = f"{args.source_host}:{args.source_port}"
    print(f"source: {src}/{dbname}   target: {host}:{port}/{dbname}")
    for role, url in source.items():
        with psycopg.connect(libpq(url), connect_timeout=10) as c:
            n = {s: len(tables(c, s)) for s in SCHEMAS if s in role}
            print(f"  source {role}: ok {n}")
    with psycopg.connect(admin, connect_timeout=10, autocommit=True) as c:
        version = c.execute("SHOW server_version").fetchone()[0]
        exists = c.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)
        ).fetchone()
        print(
            f"  target admin: ok (PostgreSQL {version}); database {dbname!r} "
            f"{'exists' if exists else 'will be created'}"
        )
        if not exists and not args.check:
            c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    if args.check:
        print("--check: nothing changed")
        return 0

    print("setup.sql (roles, extensions, schemas, grants)...")
    run_setup(admin_db, passwords)
    with psycopg.connect(admin_db) as c:
        for schema in SCHEMAS:
            if tables(c, schema):
                raise SystemExit(
                    f"target schema {schema!r} has tables; not overwriting"
                )

    with tempfile.TemporaryDirectory() as tmp:
        for schema, owner in SCHEMAS.items():
            dump = Path(tmp) / f"{schema}.dump"
            print(f"{schema}: dump as {owner}...")
            run(["pg_dump", "-Fc", "-n", schema, "-f", str(dump)], source[owner])
            listing = run(["pg_restore", "-l", str(dump)], source[owner])
            keep = Path(tmp) / f"{schema}.list"
            entries = listing.splitlines(True)
            keep.write_text("".join(e for e in entries if not SKIP_ENTRY.search(e)))
            print(f"{schema}: restore as {owner}...")
            run(
                [
                    "pg_restore",
                    "--no-owner",
                    "--single-transaction",
                    "--exit-on-error",
                    "-L",
                    str(keep),
                    "-d",
                    dbname,
                    str(dump),
                ],
                target[owner],
            )

    print("setup.sql again (grants on the restored objects)...")
    run_setup(admin_db, passwords)

    print("verify...")
    bad = 0
    for schema, owner in SCHEMAS.items():
        with (
            psycopg.connect(libpq(source[owner])) as s,
            psycopg.connect(libpq(target[owner])) as t,
        ):
            src_t, tgt_t = tables(s, schema), tables(t, schema)
            if src_t != tgt_t:
                diff = sorted(set(src_t) ^ set(tgt_t))
                print(f"  BAD {schema}: table lists differ: {diff}")
                bad += 1
            for table in src_t:
                a, b = checksum(s, schema, table), checksum(t, schema, table)
                ok = a == b
                bad += not ok
                note = "" if ok else f" (target {b[0]})"
                print(f"  {'ok ' if ok else 'BAD'} {schema}.{table}: {a[0]} rows{note}")
            for seq in stale_sequences(t, schema):
                print(f"  BAD {schema}: sequence {seq}")
                bad += 1
    print("done: every table matches" if not bad else f"FAILED: {bad} mismatch(es)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
