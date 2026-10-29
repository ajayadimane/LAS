"""Our contract for the BD dashboard. Not the vendor's shapes."""

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.schemas.common import VendorAck
from app.schemas.person import PostalAddress


class OptedService(BaseModel):
    code: str = Field(examples=["DPR_ANALYSIS"])
    name: str = Field(examples=["DPR Analysis"])
    fee: int = Field(description="Whole rupees, from our config.")


class BdCustomer(BaseModel):
    """One row of the BD's customer list.

    The three statuses are per customer, not per service, and pass the
    vendor's value through unchanged. The vendor's "NA" (not set) arrives as
    null. The full vocabulary of values is not yet known.
    """

    person_id: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    mobile_number: str | None = None
    mobile_country_code: str | None = None
    email_address: str | None = None
    address: PostalAddress = Field(default_factory=PostalAddress)
    status: str | None = Field(default=None, examples=["ACTIVE"])
    created_at: str | None = None

    services: list[OptedService] = Field(
        default_factory=list,
        description="Services the customer has opted for, in catalogue order.",
    )
    requests: list[OptedService] = Field(
        default_factory=list,
        description="Services the customer asked for themselves, waiting for "
        "this BD to authorize or decline.",
    )
    doc_status: str | None = Field(default=None, description="Doc_Status.")
    payment_status: str | None = Field(default=None, description="Payment_Status.")
    payment_mode: str | None = Field(default=None, description="Payment_Mode.")


class BdCustomerList(BaseModel):
    """`count` is the length of this response, not a total: the vendor
    documents no pagination."""

    broker_id: str
    count: int
    customers: list[BdCustomer]


class CreateCustomerRequest(BaseModel):
    """What the BD fills in. Everything else the vendor needs is ours.

    personId is the mobile number, brokerId the signed-in BD, and the program
    manager, group, country code and (placeholder) settlement account come
    from config. Constraints are the vendor's, so bad input is a 422 here
    rather than a vendor round trip.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    first_name: str = Field(min_length=1, max_length=50, examples=["Kunal"])
    last_name: str | None = Field(
        default=None, min_length=1, max_length=50, examples=["B"]
    )
    mobile_number: str = Field(
        pattern=r"^\d{10}$",
        description="Exactly 10 digits. Also becomes the customer's person id.",
        examples=["9000000013"],
    )
    email_address: EmailStr = Field(max_length=100)
    street_address: str = Field(min_length=1, max_length=100)
    city: str = Field(min_length=1, max_length=100)
    state_province: str = Field(min_length=2, max_length=100)
    postal_code: str = Field(min_length=5, max_length=12)
    country_code: str = Field(default="IN", pattern=r"^[A-Za-z]{2}$")

    @field_validator("last_name", mode="before")
    @classmethod
    def _blank_is_absent(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v


class UpdateEmailRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email_address: EmailStr = Field(max_length=100)


class UpdateAddressRequest(BaseModel):
    """The whole address: the vendor replaces it, so every part is sent.
    Same constraints as on registration."""

    model_config = ConfigDict(str_strip_whitespace=True)

    street_address: str = Field(min_length=1, max_length=100)
    city: str = Field(min_length=1, max_length=100)
    state_province: str = Field(min_length=2, max_length=100)
    postal_code: str = Field(min_length=5, max_length=12)
    country_code: str = Field(default="IN", pattern=r"^[A-Za-z]{2}$")


class UpdateCustomerResponse(BaseModel):
    person_id: str
    updated: VendorAck


class StepError(BaseModel):
    """A vendor failure on one step, shaped like every other error we send."""

    status: int
    code: str | None = None
    message: str
    transaction_id: str | None = None
    correlation_id: str | None = None


TEMPORARY_PASSWORD_DESCRIPTION = (
    "The customer's generated password, returned only here and only once: it "
    "is not stored anywhere. Hand it to the customer (mail/SMS delivery is not "
    "built yet). Null when no login was created by this call."
)


class CreateCustomerResponse(BaseModel):
    """Registration succeeded; the wallet and the login may not have.

    Writes are never retried and the vendor offers no rollback, so a later
    step failing leaves a registered customer without a wallet or a login.
    That is reported here (`wallet_opened` / `login_created` false, with the
    error) rather than as an error, and `POST /bd/customers/{id}/wallet` and
    `POST /bd/customers/{id}/login` retry just that step.
    """

    person_id: str
    registration: VendorAck
    wallet_opened: bool
    wallet: VendorAck | None = None
    wallet_error: StepError | None = None
    login_created: bool
    login_user_id: str = Field(description="What the customer signs in with.")
    login: VendorAck | None = None
    login_error: StepError | None = None
    temporary_password: str | None = Field(
        default=None, description=TEMPORARY_PASSWORD_DESCRIPTION
    )
    credentials_emailed: bool = Field(
        default=False,
        description="True once the login details were emailed to the customer.",
    )
    credentials_email_error: str | None = Field(
        default=None, description="Why the email was not sent, if it was not."
    )


class CreateLoginResponse(BaseModel):
    person_id: str
    login_user_id: str
    already_exists: bool = Field(
        description="True when the vendor said the user already exists (409). "
        "No password is returned then: the existing one is unchanged."
    )
    login: VendorAck | None = None
    temporary_password: str | None = Field(
        default=None, description=TEMPORARY_PASSWORD_DESCRIPTION
    )
    credentials_emailed: bool = Field(
        default=False,
        description="True once the login details were emailed to the customer.",
    )
    credentials_email_error: str | None = Field(
        default=None, description="Why the email was not sent, if it was not."
    )


class OpenWalletResponse(BaseModel):
    person_id: str
    already_open: bool = Field(
        description="True when the vendor said the wallet already exists (409)."
    )
    wallet: VendorAck | None = None
