"""Lead intake: geo_districts, bd_territories, leads, lead_capture_requests.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

STATEMENTS = [
    "CREATE TABLE geo_districts (\n    state     text NOT NULL,\n    district  text NOT NULL,\n    PRIMARY KEY (state, district)\n)",
    "CREATE TABLE bd_territories (\n    bd_id             bigint      NOT NULL REFERENCES bd_partners(bd_id),\n    state             text        NOT NULL,\n    district          text        NOT NULL,\n    is_active         boolean     NOT NULL DEFAULT true,\n    last_assigned_at  timestamptz,\n    PRIMARY KEY (bd_id, state, district)\n)",
    "CREATE INDEX ix_bd_territories_geo ON bd_territories(state, district) WHERE is_active",
    "CREATE SEQUENCE lead_code_seq START 1",
    "CREATE TABLE leads (\n    lead_id              bigserial PRIMARY KEY,\n    lead_code            text        NOT NULL UNIQUE\n                         DEFAULT ('LEAD-' || to_char(now(),'YYMM') || '-' ||\n                                  lpad(nextval('lead_code_seq')::text, 5, '0')),\n    mobile               varchar(10) NOT NULL CHECK (mobile ~ '^[6-9][0-9]{9}$'),\n    full_name            text,\n    state                text        NOT NULL,\n    district             text        NOT NULL,\n    interests            text[]      NOT NULL CHECK (array_length(interests, 1) > 0),\n    loan_purpose         text,\n    status               text        NOT NULL DEFAULT 'NEW'\n                         CHECK (status IN ('NEW','ASSIGNED','UNASSIGNED_PENDING','CONTACTED',\n                                           'CONVERTED','WITHDRAWN','EXPIRED')),\n    assigned_bd_id       bigint      REFERENCES bd_partners(bd_id),\n    source_inbound_id    bigint      NOT NULL REFERENCES inbound_emails(inbound_id),\n    consent_given_at     timestamptz NOT NULL,\n    consent_version      text        NOT NULL,\n    consent_text_sha256  char(64)    NOT NULL,\n    created_at           timestamptz NOT NULL DEFAULT now(),\n    updated_at           timestamptz NOT NULL DEFAULT now(),\n    deleted_at           timestamptz\n)",
    "CREATE UNIQUE INDEX uq_lead_open_mobile\n    ON leads(mobile) WHERE status NOT IN ('CONVERTED','WITHDRAWN','EXPIRED') AND deleted_at IS NULL",
    "CREATE INDEX ix_leads_assigned_bd ON leads(assigned_bd_id) WHERE assigned_bd_id IS NOT NULL",
    "CREATE TABLE lead_capture_requests (\n    request_id       bigserial PRIMARY KEY,\n    sender_email     citext      NOT NULL,\n    status           text        NOT NULL DEFAULT 'OPEN'\n                     CHECK (status IN ('OPEN','REMINDED','EXPIRED','CLOSED')),\n    opened_at        timestamptz NOT NULL DEFAULT now(),\n    reminded_at      timestamptz,\n    expires_at       timestamptz NOT NULL,\n    last_inbound_id  bigint      NOT NULL REFERENCES inbound_emails(inbound_id)\n)",
    "CREATE UNIQUE INDEX uq_lead_capture_open_sender\n    ON lead_capture_requests(sender_email) WHERE status IN ('OPEN','REMINDED')",
]

DROP = [
    "DROP TABLE IF EXISTS lead_capture_requests",
    "DROP TABLE IF EXISTS leads",
    "DROP SEQUENCE IF EXISTS lead_code_seq",
    "DROP TABLE IF EXISTS bd_territories",
    "DROP TABLE IF EXISTS geo_districts",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DROP:
        op.execute(stmt)
