"""Handoff-only: drop the old standalone workflow's tables.

This service now only reads the mailbox, checks the sender, extracts and hands each mail to Core
LAS (user decision, 2026-11-16). Customers, cases, service requests, invoices, payments, batches,
leads, IFPL referrals, DPR findings and outbound mail all live in Core LAS, so their tables, the
columns pointing at them and their sequences go. Kept: bd_partners, service_catalog,
service_synonyms, inbound_emails, email_attachments, mail_actions, ai_model_pricing, ai_usage
(+ mv_ai_usage_daily), audit_log, mailbox_checkpoints, core_intake_handoffs.

Irreversible: the dropped tables' data is not restored by a downgrade.

Revision ID: 0020_handoff_only
Revises: 0019_core_intake_handoffs
"""

from alembic import op

revision = "0020_handoff_only"
down_revision = "0019_core_intake_handoffs"
branch_labels = None
depends_on = None

DROPPED_TABLES = [
    "email_core_dpr_handoffs",
    "dpr_signatures",
    "dpr_extracted_data",
    "dpr_finding_overrides",
    "dpr_findings",
    "lead_capture_requests",
    "leads",
    "bd_territories",
    "geo_districts",
    "payment_allocations",
    "payments",
    "invoice_lines",
    "invoices",
    "reports",
    "ifpl_verification_summaries",
    "re_callbacks",
    "re_referrals",
    "sr_events",
    "sr_inputs",
    "clarification_requests",
    "outbound_emails",
    "service_requests",
    "bd_batch_rows",
    "cases",
    "bd_batches",
    "fee_schedule",
    "bd_authorizations",
    "customer_bd_links",
    "customers",
]

DROPPED_COLUMNS = {
    "inbound_emails": ["case_id"],
    "mail_actions": ["case_id", "sr_id", "batch_id"],
    "ai_usage": ["case_id", "sr_id", "batch_id"],
    # The per-service-request token budget: there are no service requests here.
    "service_catalog": ["ai_token_budget_per_sr"],
}

DROPPED_SEQUENCES = [
    "customer_code_seq",
    "batch_code_seq",
    "case_code_seq",
    "sr_code_seq",
    "invoice_no_seq",
    "lead_code_seq",
]


def upgrade():
    # asyncpg runs one statement per execute.
    op.execute("DROP VIEW IF EXISTS v_ai_cost_per_sr")
    for table in DROPPED_TABLES:
        # CASCADE removes the foreign keys from kept tables (e.g. mail_actions.case_id).
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP INDEX IF EXISTS ix_ai_usage_sr")
    for table, columns in DROPPED_COLUMNS.items():
        for column in columns:
            op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}")
    for sequence in DROPPED_SEQUENCES:
        op.execute(f"DROP SEQUENCE IF EXISTS {sequence}")


def downgrade():
    raise RuntimeError(
        "0020_handoff_only is irreversible: restore from a backup taken before upgrading"
    )
