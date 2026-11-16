"""dpr_findings gains source (RULE|LLM -- Workstream C) and confidence (rule/document provenance
level -- Workstream F, never persisted until now).

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-27
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

STATEMENTS = [
    "ALTER TABLE dpr_findings ADD COLUMN source text NOT NULL DEFAULT 'RULE'\n"
    "    CHECK (source IN ('RULE','LLM'))",
    "ALTER TABLE dpr_findings ADD COLUMN confidence text\n"
    "    CHECK (confidence IN ('placeholder','public_source','sbi_confirmed'))",
]

DROP = [
    "ALTER TABLE dpr_findings DROP COLUMN confidence",
    "ALTER TABLE dpr_findings DROP COLUMN source",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DROP:
        op.execute(stmt)
