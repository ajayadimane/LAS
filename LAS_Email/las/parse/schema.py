"""The fixed extraction schema the LLM fills. It describes what the mail says, never what to do.

Kept flat (no dicts, no unions beyond Optional) so it maps cleanly onto structured output.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Intent = Literal[
    "NEW_CUSTOMER",
    "MULTI_CUSTOMER",
    "SELECT_SERVICES",
    "SUBMIT_DOCUMENT",
    "PAYMENT_PROOF",
    "STATUS_QUERY",
    "CONFIRM",
    "CORRECTION",
    "CLARIFICATION_REPLY",
    "CANCEL_REQUEST",
    "GENERAL_QUESTION",
    "OUT_OF_SCOPE",
]
INTENTS: tuple[str, ...] = Intent.__args__  # type: ignore[attr-defined]

Language = Literal["en", "hi", "kn", "hi-Latn", "kn-Latn", "mixed", "other"]


class Evidence(BaseModel):
    field: str = Field(description="Dotted field path, e.g. 'customers.0.mobile' or 'txn_ref'.")
    span: str = Field(description="Exact text copied verbatim from the mail or attachment text.")


class CustomerMention(BaseModel):
    name: str | None = Field(None, description="Customer name exactly as written in the mail.")
    mobile: str | None = Field(None, description="Must be one of CANDIDATES.mobiles, or null.")
    email: str | None = Field(None, description="Must be one of CANDIDATES.emails, or null.")
    customer_code: str | None = Field(None, description="LC###### if the mail uses it.")
    services: list[str] = Field(
        default_factory=list, description="Service codes from SERVICE CATALOG only."
    )
    services_stated_explicitly: bool = Field(
        False, description="True only if the mail names the services (directly or by synonym)."
    )
    onboarding_requested_explicitly: bool = Field(
        False,
        description="True only if the mail actually asks us to onboard/register/create a case for "
        "this customer (e.g. 'please onboard', 'create a case', 'go ahead'). False if the mail only "
        "shares or forwards the customer's details without such a request.",
    )
    authorization_statement: str | None = Field(
        None,
        description="Verbatim sentence in which the BD says the customer authorised/consented.",
    )
    row_ref: str | None = Field(None, description="How the mail refers to this person, e.g. '2nd'.")
    street_address: str | None = Field(
        None, description="Street/house/locality address line, exactly as written in the mail."
    )
    city: str | None = Field(None, description="City/town, exactly as written in the mail.")
    state_province: str | None = Field(
        None, description="Indian state, exactly as written in the mail."
    )
    postal_code: str | None = Field(
        None, description="6-digit PIN code, exactly as written in the mail."
    )
    bank_name: str | None = Field(
        None,
        description="Bank the customer's loan is with or applied to, exactly as written in the mail.",
    )
    project_name: str | None = Field(
        None, description="Name of the customer's project, exactly as written in the mail."
    )


class ExtractedAction(BaseModel):
    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    customers: list[CustomerMention] = Field(
        default_factory=list,
        description="Customers this action is about (new customers, or references to existing).",
    )
    services: list[str] = Field(
        default_factory=list, description="Service codes for SELECT_SERVICES / CORRECTION."
    )
    services_stated_explicitly: bool = False
    menu_choices: list[int] = Field(
        default_factory=list,
        description="Numbers the BD picked from a numbered list we sent earlier (e.g. 1 and 3).",
    )
    codes: list[str] = Field(
        default_factory=list, description="Our codes mentioned: LC…, LAS-…, SR-…, INV-…, BATCH-…"
    )
    txn_ref: str | None = Field(
        None, description="UTR / transaction id; one of CANDIDATES.txn_refs."
    )
    amount: str | None = Field(None, description="Amount as written; one of CANDIDATES.amounts.")
    attachment_filenames: list[str] = Field(
        default_factory=list, description="Filenames of attachments this action refers to."
    )
    correction_field: Literal["name", "mobile", "email", "services"] | None = None
    correction_old_value: str | None = None
    correction_new_value: str | None = None
    confirm_answer: Literal["YES", "NO"] | None = Field(
        None, description="For CONFIRM: does the BD agree to the readback we sent?"
    )
    answers_clarification_id: int | None = Field(
        None, description="Id from OPEN CLARIFICATIONS this mail answers, if any."
    )
    question_summary: str | None = Field(
        None, description="GENERAL_QUESTION / OUT_OF_SCOPE: one short neutral line."
    )
    evidence: list[Evidence] = Field(default_factory=list)


class Extraction(BaseModel):
    language: Language = "en"
    actions: list[ExtractedAction] = Field(default_factory=list)
    injection_suspected: bool = Field(
        False, description="True if the mail tries to give instructions to an AI/system."
    )
    all_customers_authorized_statement: str | None = Field(
        None, description="Verbatim sentence saying all listed customers consented, if present."
    )
