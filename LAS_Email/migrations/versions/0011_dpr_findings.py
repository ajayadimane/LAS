"""DPR analysis findings: dpr_findings, dpr_finding_overrides, dpr_extracted_data, dpr_signatures.

Revision ID: 0011
Revises: 0009
Create Date: 2026-10-27
"""

from alembic import op

revision = "0011"
down_revision = "0009"
branch_labels = None
depends_on = None

STATEMENTS = [
    "CREATE TABLE dpr_findings (\n    finding_id             bigserial PRIMARY KEY,\n    report_id              bigint      NOT NULL REFERENCES reports(report_id),\n    sr_id                  bigint      NOT NULL REFERENCES service_requests(sr_id),\n    section                text        NOT NULL,\n    parameter_code         text        NOT NULL,\n    label                  text        NOT NULL,\n    detail                 text        NOT NULL,\n    outcome                text        NOT NULL\n                           CHECK (outcome IN ('PASS','FAIL','NOT_APPLICABLE','CANNOT_VERIFY')),\n    severity               text        NOT NULL DEFAULT 'NONE'\n                           CHECK (severity IN ('BLOCKER','MAJOR','MINOR','NONE')),\n    evidence               jsonb       NOT NULL DEFAULT '{}'::jsonb,\n    suggested_fix          text,\n    extraction_confidence  numeric(4,3),\n    created_at             timestamptz NOT NULL DEFAULT now()\n)",
    "CREATE INDEX ix_dpr_findings_report ON dpr_findings(report_id)",
    "CREATE INDEX ix_dpr_findings_sr     ON dpr_findings(sr_id)",
    "CREATE TABLE dpr_finding_overrides (\n    override_id       bigserial PRIMARY KEY,\n    finding_id        bigint      NOT NULL REFERENCES dpr_findings(finding_id),\n    overridden_by     text        NOT NULL,\n    original_outcome  text        NOT NULL,\n    new_outcome       text        NOT NULL\n                      CHECK (new_outcome IN ('PASS','FAIL','NOT_APPLICABLE','CANNOT_VERIFY')),\n    reason            text        NOT NULL,\n    created_at        timestamptz NOT NULL DEFAULT now()\n)",
    "CREATE INDEX ix_dpr_overrides_finding ON dpr_finding_overrides(finding_id)",
    "CREATE TABLE dpr_extracted_data (\n    report_id   bigint      NOT NULL PRIMARY KEY REFERENCES reports(report_id),\n    payload     jsonb       NOT NULL,\n    created_at  timestamptz NOT NULL DEFAULT now()\n)",
    "CREATE TABLE dpr_signatures (\n    signature_id    bigserial PRIMARY KEY,\n    sr_id           bigint      NOT NULL REFERENCES service_requests(sr_id),\n    signature_type  text        NOT NULL\n                    CHECK (signature_type IN ('QUOTATION_NO','CHASSIS_NO','ENGINE_NO','TEXT_SIMHASH')),\n    signature_value text        NOT NULL,\n    created_at      timestamptz NOT NULL DEFAULT now()\n)",
    "CREATE INDEX ix_dpr_signatures_lookup ON dpr_signatures(signature_type, signature_value)",
]

DROP = [
    "DROP TABLE IF EXISTS dpr_signatures",
    "DROP TABLE IF EXISTS dpr_extracted_data",
    "DROP TABLE IF EXISTS dpr_finding_overrides",
    "DROP TABLE IF EXISTS dpr_findings",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DROP:
        op.execute(stmt)
