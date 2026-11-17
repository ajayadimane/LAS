-- One database for Core LAS and LAS_Email: schemas, roles, privileges. Run once per environment
-- as a superuser (or the database owner with CREATEROLE), against the already-created database.
-- Idempotent: safe to run again (passwords are only set when a role is created).
--
--   createdb las_uat        # las_prod on the prod server
--   psql -d las_uat -v ON_ERROR_STOP=1 \
--        -v core_owner_password=... -v core_app_password=... \
--        -v email_owner_password=... -v email_app_password=... \
--        [-v role_prefix=uat_] -f deploy/db/setup.sql
--
-- Passwords come from the secrets store; never commit them. role_prefix is only needed when two
-- environments share one Postgres server (roles are server-wide): e.g. uat_core_app.
--
-- Layout:
--   public  extensions only (citext, pgcrypto); nobody else creates objects here
--   core    Core LAS          owner <prefix>core_owner   (migrations: python scripts/migrate.py)
--                            app   <prefix>core_app     (DPR_DATABASE_URL; rows only)
--   email   LAS_Email         owner <prefix>email_owner  (migrations: make migrate)
--                            app   <prefix>email_app    (DATABASE_URL; rows only)
-- The two services never read each other's schema: LAS_Email hands mail to Core over HTTP.

\if :{?role_prefix}
\else
\set role_prefix ''
\endif
\set core_owner :role_prefix core_owner
\set core_app :role_prefix core_app
\set email_owner :role_prefix email_owner
\set email_app :role_prefix email_app

-- Extensions: created here once, as an admin, so the service roles never need to.
CREATE EXTENSION IF NOT EXISTS citext WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;  -- LAS_Email migration 0001 names it

-- public: before PostgreSQL 15 every role may create objects in it.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- Roles (passwords only on creation; rotate with ALTER ROLE ... PASSWORD).
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'core_owner', :'core_owner_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'core_owner') \gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'core_app', :'core_app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'core_app') \gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'email_owner', :'email_owner_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'email_owner') \gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'email_app', :'email_app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'email_app') \gexec

-- Only the four roles connect to this database.
SELECT format('REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC', current_database()) \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I, %I, %I, %I', current_database(),
              :'core_owner', :'core_app', :'email_owner', :'email_app') \gexec
-- REFRESH MATERIALIZED VIEW CONCURRENTLY builds a temporary table; it runs as email_owner
-- (refresh_ai_usage_daily() is SECURITY DEFINER), so only that role gets TEMPORARY back.
SELECT format('GRANT TEMPORARY ON DATABASE %I TO %I', current_database(), :'email_owner') \gexec

-- Schemas, each owned by its service's owner role.
CREATE SCHEMA IF NOT EXISTS core AUTHORIZATION :"core_owner";
CREATE SCHEMA IF NOT EXISTS email AUTHORIZATION :"email_owner";
ALTER SCHEMA core OWNER TO :"core_owner";
ALTER SCHEMA email OWNER TO :"email_owner";
REVOKE ALL ON SCHEMA core FROM PUBLIC;
REVOKE ALL ON SCHEMA email FROM PUBLIC;
GRANT USAGE ON SCHEMA core TO :"core_app";
GRANT USAGE ON SCHEMA email TO :"email_app";

-- The app roles read and write rows in whatever their owner creates, now and later.
-- They cannot create, alter or drop anything, so they cannot remove the triggers that keep
-- dpr_report_artifacts and audit_log immutable.
ALTER DEFAULT PRIVILEGES FOR ROLE :"core_owner" IN SCHEMA core
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"core_app";
ALTER DEFAULT PRIVILEGES FOR ROLE :"core_owner" IN SCHEMA core
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO :"core_app";
ALTER DEFAULT PRIVILEGES FOR ROLE :"email_owner" IN SCHEMA email
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"email_app";
ALTER DEFAULT PRIVILEGES FOR ROLE :"email_owner" IN SCHEMA email
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO :"email_app";
-- refresh_ai_usage_daily() (LAS_Email migration 0021) revokes PUBLIC; the app role gets it here.
ALTER DEFAULT PRIVILEGES FOR ROLE :"email_owner" IN SCHEMA email
    GRANT EXECUTE ON FUNCTIONS TO :"email_app";
-- Objects that already exist (a re-run after migrations, or the local merge).
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core TO :"core_app";
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA core TO :"core_app";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA email TO :"email_app";
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA email TO :"email_app";
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA email TO :"email_app";

-- Each role lands in its own schema; extensions stay reachable through public.
-- (Both services also set this on every connection from DB_SCHEMA.)
ALTER ROLE :"core_owner" SET search_path = core, public;
ALTER ROLE :"core_app" SET search_path = core, public;
ALTER ROLE :"email_owner" SET search_path = email, public;
ALTER ROLE :"email_app" SET search_path = email, public;
