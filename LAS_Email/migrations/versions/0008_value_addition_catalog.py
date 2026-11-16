"""Resolve the "Value Addition" naming collision: APPRAISAL (the in-house DPR-driven
credit-eligibility engine) and the IFPL-routed identity/bureau/Udyam/land/small-borrowing bundle
used to share the display name "VALUE ADDITION" by historical accident. Renames APPRAISAL and
adds a new, genuinely IFPL-routed VALUE_ADDITION service -- data only, no DDL change, so
tests/integration/test_schema_parity.py (which diffs structure, not rows) is unaffected. Mirrors
las/scripts/seed.py, whose own idempotent insert-only seeding never updates an existing row.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-22
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

UPGRADE = [
    "UPDATE service_catalog SET display_name = 'Credit Appraisal' WHERE service_code = 'APPRAISAL'",
    """
    INSERT INTO service_catalog
        (service_code, service_group, display_name, menu_order, requires_re_referral,
         re_partner_code, requires_input_file, sla_business_hours, is_active)
    VALUES
        ('VALUE_ADDITION', 2, 'Value Addition', 11, true, 'IFPL', false, 24, true)
    ON CONFLICT (service_code) DO NOTHING
    """,
    """
    INSERT INTO fee_schedule (fee_code, amount, gst_pct, valid_from)
    VALUES ('VALUE_ADDITION', 500.00, 18.00, DATE '2026-04-01')
    ON CONFLICT (fee_code, valid_from) DO NOTHING
    """,
]

DOWNGRADE = [
    "DELETE FROM fee_schedule WHERE fee_code = 'VALUE_ADDITION'",
    "DELETE FROM service_catalog WHERE service_code = 'VALUE_ADDITION'",
    "UPDATE service_catalog SET display_name = 'VALUE ADDITION' WHERE service_code = 'APPRAISAL'",
]


def upgrade() -> None:
    for stmt in UPGRADE:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE:
        op.execute(stmt)
