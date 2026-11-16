"""Add SURVEY_NUMBER to dpr_signatures.signature_type (Workstream A: exact-match land-survey-number
duplicate detection across applications, alongside quotation/chassis/engine number).

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-27
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

STATEMENTS = [
    "ALTER TABLE dpr_signatures DROP CONSTRAINT dpr_signatures_signature_type_check",
    "ALTER TABLE dpr_signatures ADD CONSTRAINT dpr_signatures_signature_type_check\n"
    "    CHECK (signature_type IN ('QUOTATION_NO','CHASSIS_NO','ENGINE_NO','SURVEY_NUMBER','TEXT_SIMHASH'))",
]

DROP = [
    "ALTER TABLE dpr_signatures DROP CONSTRAINT dpr_signatures_signature_type_check",
    "ALTER TABLE dpr_signatures ADD CONSTRAINT dpr_signatures_signature_type_check\n"
    "    CHECK (signature_type IN ('QUOTATION_NO','CHASSIS_NO','ENGINE_NO','TEXT_SIMHASH'))",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DROP:
        op.execute(stmt)
