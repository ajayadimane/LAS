# One database for Core LAS and LAS_Email

Each environment has **one** PostgreSQL database (`las_uat`, `las_prod`). It holds both services,
each in its own schema and under its own roles:

| Schema | Holds | Owner (runs migrations) | App role (rows only) |
|---|---|---|---|
| `public` | extensions only (`citext`, `pgcrypto`) | DBA | none |
| `core` | Core LAS: DPR jobs and reports, intake events and cases, pay-later dues | `core_owner` | `core_app` |
| `email` | LAS_Email: inbound mail, attachments, AI usage, audit log, handoffs | `email_owner` | `email_app` |

- **The services still talk over HTTP.** LAS_Email posts each mail to
  `/internal/v1/intake/events`. Neither service can read the other's schema.
- **The app roles cannot change the schema.** They cannot create, alter or drop tables, and cannot
  remove the immutability triggers on `core.dpr_report_artifacts` and `email.audit_log`.
- **Migrations are a deploy step, run as the owner role.**
- **Every connection sets its `search_path`** from the service's `DB_SCHEMA`, and the roles have
  the same default (`setup.sql`).

## 1. Create the database (once per environment, as a DBA)
```bash
createdb las_uat
psql -d las_uat -v ON_ERROR_STOP=1 \
  -v core_owner_password=... -v core_app_password=... \
  -v email_owner_password=... -v email_app_password=... \
  -f deploy/db/setup.sql
```
- **Passwords:** take them from the secrets store; they never go in git.
- **Shared server:** if UAT and prod share one Postgres server, add `-v role_prefix=uat_` (roles
  are server-wide). This gives `uat_core_app` and so on.
- **Safe to re-run:** `setup.sql` is idempotent, and re-running it also grants on objects that
  already exist.

## 2. Settings
Core `.env`:
```
DPR_DATABASE_URL=postgresql+psycopg://core_app:...@db-host:5432/las_uat
DB_SCHEMA=core
DB_MIGRATE_ON_STARTUP=false
DB_MIGRATION_URL=postgresql+psycopg://core_owner:...@db-host:5432/las_uat
```
LAS_Email `.env`:
```
DATABASE_URL=postgresql+asyncpg://email_app:...@db-host:5432/las_uat
DB_SCHEMA=email
MIGRATION_DATABASE_URL=postgresql+asyncpg://email_owner:...@db-host:5432/las_uat
```

## 3. Every deploy: migrate as the owners, then start the apps
```bash
python scripts/migrate.py                       # Core: migrations/*.sql into schema core
(cd LAS_Email && make db-check migrate seed)    # LAS_Email: Alembic into schema email
```
- **Both are idempotent.** If the schema is missing or the migrations have not run, Core refuses to
  start that part with `SchemaNotReady`. LAS_Email's Alembic refuses with a `current schema`
  error.
- **Docker:** run the Core step with
  `docker run --rm --network host --env-file ... las-backend python scripts/migrate.py`.

## 4. Backups
- **Whole environment:** `pg_dump -Fc las_uat`. It restores both services together, which keeps
  them consistent.
- **One service only:** `pg_dump -Fc -n core las_uat` or `-n email`.
- **Not in the database:** LAS_Email's encrypted file store and its `LOCAL_STORE_KEY_B64` are
  backed up separately (`LAS_Email/deploy/README.md`).

## 5. Connections
Each process keeps its own pool:
- **Core:** the API, plus the DPR worker if it runs separately.
- **LAS_Email:** the worker, beat and ingest processes, with `DB_POOL_SIZE` each (default 10).

Size `max_connections` for the sum of these pools, or put PgBouncer in front.

## 6. Moving an existing two-database setup (development only)
`scripts/merge_email_db.py` moves Core's tables from `public` to `core`, and copies the old
`LAS_Email` database into `email`. It compares a checksum of every table. Each step is one
transaction, and `--dry-run` rolls everything back. The steps are listed in its docstring:
1. **Back up and stop.** `pg_dump -Fc` both databases, then stop Core, the DPR worker and
   LAS_Email.
2. **Create roles and schemas.** Run `setup.sql` against `LAS`.
3. **Move Core.** Run `merge_email_db.py --move-core`.
4. **Migrate LAS_Email.** Run `make migrate` with `DB_SCHEMA=email` as `email_owner`.
5. **Copy LAS_Email.** Run `merge_email_db.py --copy-email`.
6. **Grant again.** Run `setup.sql` once more.
7. **Switch and start.** Point both `.env` files at the app roles with `DB_SCHEMA`, and start.

Keep the old `LAS_Email` database until everything has been checked, then drop it.
