"""Track async Core LAS DPR runs and preserve the accepted submission state.

Revision ID: 0018_core_dpr_async_handoff
Revises: 0017_email_core_dpr_handoff
"""
from alembic import op

revision = "0018_core_dpr_async_handoff"
down_revision = "0017_email_core_dpr_handoff"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE email_core_dpr_handoffs ADD COLUMN core_run_id TEXT")
    op.execute("ALTER TABLE email_core_dpr_handoffs DROP CONSTRAINT IF EXISTS email_core_dpr_handoffs_status_check")
    op.execute("""ALTER TABLE email_core_dpr_handoffs ADD CONSTRAINT email_core_dpr_handoffs_status_check
        CHECK (status IN ('PENDING','IN_FLIGHT','WAITING_CUSTOMER','WAITING_CORE',
                         'QUEUED','IN_FLIGHT_POLL','COMPLETED','RECONCILE','FAILED_PERMANENT'))""")
    op.execute("""CREATE INDEX ix_email_core_dpr_poll ON email_core_dpr_handoffs(next_attempt_at)
                 WHERE status='QUEUED'""")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_email_core_dpr_poll")
    op.execute("UPDATE email_core_dpr_handoffs SET status='RECONCILE' WHERE status='QUEUED'")
    op.execute("ALTER TABLE email_core_dpr_handoffs DROP CONSTRAINT IF EXISTS email_core_dpr_handoffs_status_check")
    op.execute("""ALTER TABLE email_core_dpr_handoffs ADD CONSTRAINT email_core_dpr_handoffs_status_check
        CHECK (status IN ('PENDING','IN_FLIGHT','WAITING_CUSTOMER','WAITING_CORE',
                         'COMPLETED','RECONCILE','FAILED_PERMANENT'))""")
    op.execute("ALTER TABLE email_core_dpr_handoffs DROP COLUMN core_run_id")
