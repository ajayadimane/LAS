"""Add iWallet Person Registration response fields to customers.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-01
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

STATEMENTS = [
    "ALTER TABLE customers ADD COLUMN iwallet_response_code smallint",
    "ALTER TABLE customers ADD COLUMN iwallet_transaction_id text",
    "ALTER TABLE customers ADD COLUMN iwallet_correlation_id text",
    "ALTER TABLE customers ADD COLUMN iwallet_error text",
    "ALTER TABLE customers ADD COLUMN iwallet_registered_at timestamptz",
]

DROP = [
    "ALTER TABLE customers DROP COLUMN IF EXISTS iwallet_registered_at",
    "ALTER TABLE customers DROP COLUMN IF EXISTS iwallet_error",
    "ALTER TABLE customers DROP COLUMN IF EXISTS iwallet_correlation_id",
    "ALTER TABLE customers DROP COLUMN IF EXISTS iwallet_transaction_id",
    "ALTER TABLE customers DROP COLUMN IF EXISTS iwallet_response_code",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DROP:
        op.execute(stmt)
