"""The one-time joining fee is a flat Rs 295 with no GST -- it was inheriting the uniform 18% GST
every other fee_schedule row gets, which only became visible once the portal started surfacing it
as its own line item in the Pay Now/Pay Later breakdown (previously it was silently bundled into a
completed SR's invoice total). Data only, no DDL change. Mirrors las/scripts/seed.py, whose own
idempotent insert-only seeding never updates an existing row.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-22
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

UPGRADE = "UPDATE fee_schedule SET gst_pct = 0.00 WHERE fee_code = 'JOINING_FEE'"
DOWNGRADE = "UPDATE fee_schedule SET gst_pct = 18.00 WHERE fee_code = 'JOINING_FEE'"


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
