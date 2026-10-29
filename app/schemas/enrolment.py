"""Our contract for the service catalogue, opting in, payment and documents.

Not the vendor's shapes. On the vendor all of this is person custom fields;
see app/services/enrolment.py.
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import VendorAck
from app.schemas.document import FileInfo

PaymentMode = Literal["pay_now", "pay_later"]


class CatalogService(BaseModel):
    code: str = Field(examples=["GEO_CHECK"])
    name: str
    fee: int = Field(description="Whole rupees.")
    description: str
    external: bool = Field(
        description="Paid and documented on a partner portal, not here."
    )
    portal_url: str | None = None


class Catalog(BaseModel):
    """The services the dashboard sells. From our config, not the vendor."""

    joining_fee: int = Field(description="One-time fee, whole rupees.")
    services: list[CatalogService]


class CustomerService(BaseModel):
    """One in-house service, for one customer. Statuses are the vendor's raw
    values ("NA" arrives as null)."""

    code: str
    name: str
    fee: int
    opted: bool
    requested: bool = Field(
        default=False,
        description="The customer asked for it themselves; waiting for the BD.",
    )
    status: str | None = Field(
        default=None, description="Lifecycle, e.g. Opted, Docs uploaded, Approved."
    )
    payment_status: str | None = Field(default=None, examples=["Paid"])
    payment_mode: str | None = Field(default=None, examples=["Pay later"])
    transaction_id: str | None = Field(
        default=None, description="The pool transfer, for pay later."
    )
    document: FileInfo | None = Field(
        default=None, description="The uploaded document, if any. No bytes."
    )
    can_upload: bool = Field(
        description="Paid, and not yet approved: a document may be uploaded."
    )


class CustomerServices(BaseModel):
    """Where one customer is in opting, payment and documents.

    The three customer-level statuses are the summary the dashboard filters
    on; "NA" arrives as null.
    """

    person_id: str
    services: list[CustomerService]
    payment_status: str | None = None
    payment_mode: str | None = None
    doc_status: str | None = None
    first_payment: bool = Field(
        description="Nothing paid yet: the next payment includes the joining fee."
    )
    payment_in_progress: bool = Field(
        description="A pay-later transfer was started and its outcome is not "
        "recorded. Needs support to reconcile; no new payment is allowed."
    )


class PaymentRequest(BaseModel):
    """Opt into services and pay for them: the first time, or adding more.

    Only services not yet opted may be listed. The joining fee is charged on
    the first payment only.
    """

    services: list[str] = Field(
        min_length=1,
        description="Codes of the in-house services to opt into. Partner-portal "
        "services are paid there and are rejected here.",
        examples=[["DPR_ANALYSIS", "GEO_CHECK"]],
    )
    mode: PaymentMode
    bd_consent: bool = Field(
        default=False, description="Pay later only: the BD agrees."
    )
    customer_consent: bool = Field(
        default=False, description="Pay later only: the customer agrees."
    )


class PaymentResponse(BaseModel):
    person_id: str
    mode: PaymentMode
    services: list[str]
    amount: int = Field(
        description="Whole rupees: the services, plus the joining fee on the "
        "first payment only."
    )
    joining_fee: int = Field(
        description="The part of `amount` that is the joining fee."
    )
    payment_status: str = Field(description="As written to Payment_Status.")
    transfer: VendorAck | None = Field(
        default=None,
        description="Pay later: the pool-to-main transfer. Quote its ids to support.",
    )
    status_recorded: bool = Field(
        default=True,
        description="False if the transfer succeeded but marking the customer "
        "paid did not. The money has moved: do not pay again; contact support.",
    )


class ServiceRequest(BaseModel):
    """A signed-in customer opting into services themselves."""

    services: list[str] = Field(min_length=1, examples=[["GEO_CHECK"]])
    mode: PaymentMode
    customer_consent: bool = Field(
        default=False, description="Pay later only: the customer promises to pay."
    )


class ServiceRequestResponse(BaseModel):
    person_id: str
    mode: PaymentMode
    services: list[str]
    amount: int = Field(
        description="Whole rupees, as it stands now (joining fee included if "
        "nothing has been paid yet)."
    )
    awaiting_bd: bool = Field(
        description="True: the BD must authorize before anything is charged."
    )
    payment: PaymentResponse | None = None


class AuthorizeRequest(BaseModel):
    bd_consent: bool = Field(
        default=False,
        description="Pay later: the BD stands guarantor for the customer's fee.",
    )


class DocumentUploadResponse(BaseModel):
    person_id: str
    code: str
    document: FileInfo
    doc_status: str = Field(description="As written to Doc_Status.")
