"""customers: postal address + LAS Middleware registration bookkeeping (MIDDLEWARE_API.md).

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-28
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

STATEMENTS = [
    "ALTER TABLE customers ADD COLUMN street_address text",
    "ALTER TABLE customers ADD COLUMN city text",
    "ALTER TABLE customers ADD COLUMN state_province text",
    "ALTER TABLE customers ADD COLUMN postal_code text",
    "ALTER TABLE customers ADD COLUMN middleware_person_id text",
    "ALTER TABLE customers ADD COLUMN middleware_status text NOT NULL DEFAULT 'PENDING' "
    "CHECK (middleware_status IN ('PENDING','DONE','FAILED'))",
    "ALTER TABLE customers ADD COLUMN middleware_error text",
    "ALTER TABLE customers ADD COLUMN middleware_registered_at timestamptz",
]

DROP = [
    "ALTER TABLE customers DROP COLUMN middleware_registered_at",
    "ALTER TABLE customers DROP COLUMN middleware_error",
    "ALTER TABLE customers DROP COLUMN middleware_status",
    "ALTER TABLE customers DROP COLUMN middleware_person_id",
    "ALTER TABLE customers DROP COLUMN postal_code",
    "ALTER TABLE customers DROP COLUMN state_province",
    "ALTER TABLE customers DROP COLUMN city",
    "ALTER TABLE customers DROP COLUMN street_address",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DROP:
        op.execute(stmt)
