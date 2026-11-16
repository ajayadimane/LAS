"""Add 9 statutory-clearance doc_class values (Workstream E): NA permission, building plan
approval, pollution consent, fire NOC, electricity load sanction, groundwater NOC, dairy
cooperative tie-up letter, encumbrance certificate, lease deed.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-27
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

NEW_VALUES = (
    "'DPR','PAN','AADHAAR','UDYAM','LAND_RECORD','QUOTATION',"
    "'BANK_STATEMENT','PAYMENT_PROOF','CONSENT_FORM','BATCH_SHEET',"
    "'NA_PERMISSION','BUILDING_PLAN_APPROVAL','POLLUTION_CONSENT','FIRE_NOC',"
    "'ELECTRICITY_LOAD_SANCTION','GROUNDWATER_NOC','DAIRY_COOP_TIEUP_LETTER',"
    "'ENCUMBRANCE_CERTIFICATE','LEASE_DEED','OTHER'"
)
OLD_VALUES = (
    "'DPR','PAN','AADHAAR','UDYAM','LAND_RECORD','QUOTATION',"
    "'BANK_STATEMENT','PAYMENT_PROOF','CONSENT_FORM','BATCH_SHEET','OTHER'"
)

STATEMENTS = [
    "ALTER TABLE email_attachments DROP CONSTRAINT email_attachments_doc_class_check",
    f"ALTER TABLE email_attachments ADD CONSTRAINT email_attachments_doc_class_check\n"
    f"    CHECK (doc_class IN ({NEW_VALUES}))",
]

DROP = [
    "ALTER TABLE email_attachments DROP CONSTRAINT email_attachments_doc_class_check",
    f"ALTER TABLE email_attachments ADD CONSTRAINT email_attachments_doc_class_check\n"
    f"    CHECK (doc_class IN ({OLD_VALUES}))",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DROP:
        op.execute(stmt)
