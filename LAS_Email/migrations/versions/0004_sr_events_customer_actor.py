"""sr_events.actor_type -- widen to allow 'CUSTOMER' (a customer's own action, e.g. cancelling
their own service request, can itself be the actor of an SR state transition).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-28
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

STATEMENTS = [
    "ALTER TABLE sr_events DROP CONSTRAINT sr_events_actor_type_check",
    "ALTER TABLE sr_events ADD CONSTRAINT sr_events_actor_type_check "
    "CHECK (actor_type IN ('SYSTEM','BD','OPS','CREDIT_OFFICER','RE_PARTNER','CUSTOMER'))",
]

DOWNGRADE_STATEMENTS = [
    "ALTER TABLE sr_events DROP CONSTRAINT sr_events_actor_type_check",
    "ALTER TABLE sr_events ADD CONSTRAINT sr_events_actor_type_check "
    "CHECK (actor_type IN ('SYSTEM','BD','OPS','CREDIT_OFFICER','RE_PARTNER'))",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE_STATEMENTS:
        op.execute(stmt)
