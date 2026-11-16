"""leads.pincode -- optional 6-digit PIN code collected on the lead intake web form.

Revision ID: 0007
Revises: 0004
Create Date: 2026-10-20
"""

from alembic import op

revision = "0007"
down_revision = "0004"
branch_labels = None
depends_on = None

STATEMENTS = [
    "ALTER TABLE leads ADD COLUMN pincode varchar(6)",
    "ALTER TABLE leads ADD CONSTRAINT leads_pincode_check CHECK (pincode ~ '^[1-9][0-9]{5}$')",
]

DROP = [
    "ALTER TABLE leads DROP CONSTRAINT leads_pincode_check",
    "ALTER TABLE leads DROP COLUMN pincode",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DROP:
        op.execute(stmt)
