"""refresh_ai_usage_daily(): lets a non-owner role refresh mv_ai_usage_daily.

In the shared UAT/prod database (Core LAS deploy/db/setup.sql) the services connect as
``email_app``, which may only read and write rows; REFRESH MATERIALIZED VIEW needs the view's
owner. This function runs as its owner (SECURITY DEFINER) with its search_path pinned to the
schema it was created in, and does nothing but that one refresh.

Revision ID: 0021_refresh_usage_function
Revises: 0020_handoff_only
"""

from alembic import op

revision = "0021_refresh_usage_function"
down_revision = "0020_handoff_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
CREATE OR REPLACE FUNCTION refresh_ai_usage_daily() RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_ai_usage_daily;
END;
$$"""
    )
    # Functions are executable by PUBLIC by default; only roles granted it may refresh.
    op.execute("REVOKE ALL ON FUNCTION refresh_ai_usage_daily() FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS refresh_ai_usage_daily()")
