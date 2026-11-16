"""The structured KYC/bureau/bank-statement summary LAS pulls from IFPL (Workstream B).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-27
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

STATEMENTS = [
    "CREATE TABLE ifpl_verification_summaries (\n    summary_id      bigserial PRIMARY KEY,\n    referral_id     bigint      NOT NULL REFERENCES re_referrals(referral_id),\n    payload         jsonb       NOT NULL,\n    signature_valid boolean     NOT NULL,\n    pulled_at       timestamptz NOT NULL,\n    created_at      timestamptz NOT NULL DEFAULT now()\n)",
    "CREATE INDEX ix_ifpl_verification_summaries_referral\n    ON ifpl_verification_summaries(referral_id, pulled_at DESC)",
]

DROP = [
    "DROP TABLE IF EXISTS ifpl_verification_summaries",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DROP:
        op.execute(stmt)
