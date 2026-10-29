"""Application settings. Everything the app needs comes from here or .env."""

from functools import lru_cache
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceConfig(BaseModel):
    """One LAS service the dashboard sells. Fees are whole rupees.

    An in-house service is a set of person custom fields named after
    `field_id`; see app/services/service_fields.py. Adding a service = an entry
    here + `python scripts/setup_custom_fields.py --apply`.
    """

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    name: str
    fee: int = Field(ge=0)
    description: str = ""
    # Handled on a partner portal: documents and payment happen there.
    external: bool = False
    portal_url: str | None = None
    # The vendor custom field that records this service on a person. "NA" or
    # empty means not opted. None for portal services, which have no field.
    field_id: str | None = None


DEFAULT_SERVICES = [
    ServiceConfig(
        code="DPR_GENERATION",
        name="DPR Generation",
        fee=2000,
        description="We prepare a bank-ready Detailed Project Report for your "
        "loan application.",
        field_id="DPR_Creation",
    ),
    ServiceConfig(
        code="DPR_ANALYSIS",
        name="DPR Analysis",
        fee=1000,
        description="Review your existing Project Report and flag gaps before "
        "you submit it.",
        field_id="DPR_Analysis",
    ),
    ServiceConfig(
        code="GEO_CHECK",
        name="Geo Check",
        fee=500,
        description="Based on location, we analyse whether the loan requirement "
        "is valid.",
        field_id="GeoCheck",
    ),
    ServiceConfig(
        code="VALUE_ADDITION",
        name="Value Addition",
        fee=500,
        description="Verify KYC and credit scores, avail short-term loan "
        "facilities, and lend short-term loans.",
        external=True,
        portal_url="https://if-p2p.com/login",
    ),
    ServiceConfig(
        code="DOC_VERIFICATION",
        name="Doc Verification",
        fee=500,
        description="Validate the third-party quotation.",
        field_id="Doc_Verification",
    ),
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "LAS Dashboard Middleware"
    environment: str = "local"
    log_level: str = "INFO"
    # Path the app is served under, e.g. "/las-api" -> /las-api/api/v1/...,
    # /las-api/health, /las-api/docs. Empty = served at the root. Use this when
    # the reverse proxy forwards the full path; if it strips the prefix, leave
    # this empty and start uvicorn with --root-path instead.
    context_path: str = "/las-api"

    # --- Vendor (iWallet) -------------------------------------------------
    # Only one host appears in api_docs/. Sandbox vs production split is
    # UNKNOWN - ask user.
    vendor_base_url: str = "https://10.10.20.153:8080"
    vendor_timeout_seconds: float = 10.0
    vendor_connect_timeout_seconds: float = 5.0
    vendor_max_connections: int = 20

    # The vendor host is an RFC-1918 address and its certificate may be
    # self-signed. UNKNOWN - ask user. Kept True by default so that turning
    # verification off is always a deliberate, visible config change.
    vendor_verify_ssl: bool = True

    # Default program manager used when the dashboard does not send one.
    vendor_program_manager_id: str = "LAS"

    # Super admin, used server-side only, to create customer logins: the IAM
    # add-user call needs Keycloak's manage-users role, which a BD token lacks
    # (verified live 2026-10-27). Never sent to the dashboard. Unset = customer
    # login creation fails with ADMIN_NOT_CONFIGURED.
    vendor_admin_user_id: str | None = None
    vendor_admin_password: SecretStr | None = None

    # --- Outgoing mail (customer login details) ----------------------------
    # Zoho Mail over SMTP. Port 465 = implicit TLS, 587 = STARTTLS; TLS is
    # always verified. For a mailbox with 2-factor sign-in, the password is a
    # Zoho app password. Unset password = no mail is sent (reported, not an
    # error).
    smtp_host: str = "smtp.zoho.in"
    smtp_port: int = 465
    smtp_username: str | None = None  # defaults to mail_from
    smtp_password: SecretStr | None = None
    smtp_timeout_seconds: float = 15.0
    mail_from: str = "las@integramicro.co.in"
    mail_from_name: str = "LAS"
    # Where customers sign in; put in the login-details email.
    dashboard_sign_in_url: str = "https://demo.botaiml.com/las/signin"

    # --- Customer onboarding (POST /api/v1/bd/customers) -------------------
    # The person's business group (not the IAM groupName) and the tag of the
    # wallet opened for them. One wallet per tag per person.
    vendor_group_id: str = "group1"
    vendor_wallet_tag_id: str = "tag1"
    customer_mobile_country_code: str = "91"
    # IAM groups ('<programManagerId>.enduser' / '.admin'), not the person's
    # groupId above. A customer's login is created in the first; the token's
    # `groupId` claim tells a customer from a BD at sign-in (app/core/roles.py).
    customer_iam_group_name: str = "LAS.enduser"
    bd_iam_group_name: str = "LAS.admin"
    # Length of the temporary password generated for a new customer login.
    # The vendor's password policy is undocumented; the generated value mixes
    # upper, lower, digit and symbol to satisfy the usual Keycloak rules.
    customer_password_length: int = Field(default=12, ge=8, le=64)
    # The vendor requires a settlement account on every person. Until the
    # dashboard collects a real one, every customer gets this placeholder
    # (user, 2026-10-27). Replace before production.
    default_settlement_account_no: str = "1234567890"
    default_settlement_ifsc_code: str = "ICIC0001010"

    # --- Opting, payment, documents (user, 2026-10-27) -----------------------
    # Values written to the person's custom fields; registration writes "NA"
    # everywhere. Each in-house service has its own fields (see
    # app/services/service_fields.py); the three customer-level ones are a
    # summary recomputed on every write.
    #
    # Service lifecycle, in the service's own field (e.g. GeoCheck):
    # A customer's own request, waiting for their BD (user, 2026-10-28).
    service_requested_value: str = "Awaiting BD"
    service_opted_value: str = "Opted"
    service_docs_uploaded_value: str = "Docs uploaded"
    service_approved_value: str = "Approved"
    service_rejected_value: str = "Rejected"
    service_report_ready_value: str = "Report ready"
    # Payment, per service (<field>_Payment) and summarised in Payment_Status.
    payment_status_paid: str = "Paid"
    # Written before a pay-later transfer, so a second payment is refused
    # while the first one's outcome is unknown.
    payment_status_processing: str = "Processing"
    payment_mode_pay_now: str = "Pay now"
    payment_mode_pay_later: str = "Pay later"
    # Payment_Mode when a customer's services were paid in different ways.
    payment_mode_mixed: str = "Mixed"
    # A customer's own Pay later always needs their BD to authorize (stand
    # guarantor). Their Pay now does not -- but while the QR is a placeholder
    # nothing proves they paid, so until the real QR is integrated their BD
    # confirms receipt. Set False once a real payment confirmation exists.
    customer_pay_now_needs_confirmation: bool = True
    doc_status_uploaded: str = "Uploaded"
    doc_status_partial: str = "Partially uploaded"

    # Pay later moves the amount from the pool (a system account) to the main
    # account wallet: PUT /transfer/systemAccountPayment.
    vendor_pool_transfer_type_id: str = "pool_to_main"
    # Empty in the user's sample: the transfer type names the pool account.
    vendor_pool_debit_from: str = ""
    vendor_main_account_id: str = "IntegraMain"
    vendor_main_account_tag_id: str = "tag1"
    vendor_main_account_group_id: str = "group1"

    # Geolocation headers are mandatory on every vendor call. Whether the
    # dashboard supplies real end-user coordinates is UNKNOWN - ask user;
    # until then these are sent as a fallback.
    default_geo_latitude: str = "12.97"
    default_geo_longitude: str = "77.59"

    # Retries apply only to read-only /walletInfoRetrieval paths (see
    # VendorClient). The vendor documents no idempotency key, so writes are
    # never retried.
    vendor_retry_attempts: int = 3
    vendor_retry_backoff_seconds: float = 0.2

    # --- LAS services ------------------------------------------------------
    # The catalogue and its fees are ours, not the vendor's. Override with a
    # JSON list in the SERVICES env var.
    services: list[ServiceConfig] = Field(
        default_factory=lambda: [s.model_copy() for s in DEFAULT_SERVICES]
    )
    joining_fee: int = 295

    # Documents are stored base64-encoded in the service's custom field (user
    # spec, 2026-10-27). The vendor's real limit on a field value is UNKNOWN -
    # ask user; this is our own cap. Base64 adds a third on top.
    # 2 MB while documents live in vendor custom fields: every customer-list
    # call returns them (user decision, 2026-10-27; see service_fields.py).
    document_max_bytes: int = 2 * 1024 * 1024
    document_allowed_types: list[str] = Field(
        default_factory=lambda: ["application/pdf", "image/jpeg", "image/png"]
    )

    @field_validator("context_path")
    @classmethod
    def _normalise_context_path(cls, v: str) -> str:
        """Leading slash, no trailing one: "las-api/" -> "/las-api"; "/" -> ""."""
        v = v.strip().strip("/")
        return f"/{v}" if v else ""

    @field_validator("vendor_base_url")
    @classmethod
    def _normalise_base_url(cls, v: str) -> str:
        """Drop a trailing slash, and a trailing dot on the host.

        `https://10.10.20.153.:8080` (as the host has been written) does not
        resolve: an IP literal cannot end in a dot. Fails with "Name or service
        not known", which surfaces only as "Vendor is unreachable".
        """
        parts = urlsplit(v.rstrip("/"))
        host = parts.hostname or ""
        if host.endswith("."):
            netloc = parts.netloc.replace(host, host.rstrip("."), 1)
            parts = parts._replace(netloc=netloc)
        return urlunsplit(parts)


@lru_cache
def get_settings() -> Settings:
    return Settings()
