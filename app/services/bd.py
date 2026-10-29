"""The BD dashboard: the customers registered under one broker.

Adding a customer is three vendor writes: person registration, wallet
creation, login creation (IAM user). Only the first is fatal.

Each customer's services and statuses are person custom fields; see
app/services/service_fields.py. The list shows a service as opted when its
lifecycle field is set, plus the three customer-level summary statuses.
Field values are never passed through raw: `<F>_Doc` holds a whole document.
"""

from pydantic import SecretStr

from app.clients.vendor_client import VendorCallContext, VendorClient
from app.core.config import Settings
from app.core.exceptions import VendorError, error_details
from app.core.logging import get_logger
from app.core.passwords import temporary_password
from app.schemas.bd import (
    BdCustomer,
    BdCustomerList,
    CreateCustomerRequest,
    CreateCustomerResponse,
    CreateLoginResponse,
    OpenWalletResponse,
    OptedService,
    StepError,
    UpdateAddressRequest,
    UpdateCustomerResponse,
    UpdateEmailRequest,
)
from app.schemas.common import VendorAck
from app.schemas.customer import CustomerProfile
from app.schemas.identity import CreateUserRequest
from app.schemas.person import PersonDetails
from app.services.admin_session import AdminSession
from app.services.credentials_mail import credentials_mail
from app.services.documents import custom_field_values
from app.services.identity import IdentityService
from app.services.mailer import Mailer, MailNotConfiguredError, MailSendError
from app.services.person import BY_BROKER_ID_PATH, BY_PERSON_ID_PATH, REGISTER_PATH
from app.services.service_fields import (
    DOC_STATUS_FIELD,
    NOT_SET,
    PAYMENT_MODE_FIELD,
    PAYMENT_STATUS_FIELD,
    all_field_ids,
    field_value,
    is_opted,
    is_requested,
)

logger = get_logger(__name__)

WALLET_PATH = "/regularWallet/"
# Both PUT (Postman collection, user spec 2026-10-28). No trailing slash.
EMAIL_PATH = "/customermgmt/person/emailAddress"
ADDRESS_PATH = "/customermgmt/person/address"


class CustomerNotFoundError(LookupError):
    """No such person, or not one of this BD's customers. Deliberately one
    error, so a BD cannot probe which person ids exist under other brokers."""


class CustomerIncompleteError(ValueError):
    """The vendor's record lacks something a step needs."""


class BdService:
    def __init__(
        self,
        client: VendorClient,
        settings: Settings,
        admin: AdminSession,
        mailer: Mailer | None = None,
    ) -> None:
        self._client = client
        # Emails the login details; None = never sent (reported as such).
        self._mailer = mailer
        self._settings = settings
        self._identity = IdentityService(client, settings)
        # Login creation needs the super admin's token, not the BD's.
        self._admin = admin

    def _customer(self, item: object) -> BdCustomer:
        return self._from_person(PersonDetails.from_vendor(item), item)

    def _from_person(self, person: PersonDetails, item: object) -> BdCustomer:
        fields = custom_field_values(item)

        def listed(test) -> list[OptedService]:
            return [
                OptedService(code=s.code, name=s.name, fee=s.fee)
                for s in self._settings.services
                if s.field_id and test(fields.get(s.field_id), self._settings)
            ]

        return BdCustomer(
            person_id=person.person_id,
            first_name=person.first_name,
            last_name=person.last_name,
            mobile_number=person.mobile_number,
            mobile_country_code=person.mobile_country_code,
            email_address=person.email_address,
            address=person.address,
            status=person.status,
            created_at=person.created_at,
            services=listed(is_opted),
            requests=listed(is_requested),
            doc_status=field_value(fields.get(DOC_STATUS_FIELD)),
            payment_status=field_value(fields.get(PAYMENT_STATUS_FIELD)),
            payment_mode=field_value(fields.get(PAYMENT_MODE_FIELD)),
        )

    async def customers(self, broker_id: str, ctx: VendorCallContext) -> BdCustomerList:
        payload = {
            "brokerId": broker_id,
            "programManagerId": self._settings.vendor_program_manager_id,
        }
        body = await self._client.post(BY_BROKER_ID_PATH, payload, ctx)
        items = body if isinstance(body, list) else []
        customers = [self._customer(i) for i in items if isinstance(i, dict)]
        # Newest first. ISO-8601 UTC strings sort correctly as text.
        customers.sort(key=lambda c: c.created_at or "", reverse=True)
        return BdCustomerList(
            broker_id=broker_id, count=len(customers), customers=customers
        )

    async def customer(
        self, person_id: str, broker_id: str, ctx: VendorCallContext
    ) -> BdCustomer:
        """One customer, only if registered under this broker."""
        person, body = await self.owned_person(person_id, broker_id, ctx)
        return self._from_person(person, body)

    async def owned_person(
        self, person_id: str, broker_id: str, ctx: VendorCallContext
    ) -> tuple[PersonDetails, dict]:
        """The person and their raw vendor body, only if under this broker.

        The vendor returns any person in the program manager to any valid
        token, so the ownership check is ours: `brokerId.id` must be this BD.
        The raw body is for reading custom fields; never return it.
        """
        person, body = await self.person(person_id, ctx)
        # Fail closed: a body without a broker belongs to no BD.
        if not person.broker_id or person.broker_id.casefold() != broker_id.casefold():
            raise CustomerNotFoundError(person_id)
        return person, body

    async def person(
        self, person_id: str, ctx: VendorCallContext
    ) -> tuple[PersonDetails, dict]:
        """The person and their raw vendor body, with no ownership check.

        Only for a customer acting on their own record (person id = the
        token's user); a BD goes through `owned_person`.
        """
        payload = {
            "personId": person_id,
            "programManagerId": self._settings.vendor_program_manager_id,
        }
        try:
            body = await self._client.post(BY_PERSON_ID_PATH, payload, ctx)
        except VendorError as exc:
            if exc.http_status == 404:
                raise CustomerNotFoundError(person_id) from None
            raise
        if not isinstance(body, dict):
            raise CustomerNotFoundError(person_id)
        return PersonDetails.from_vendor(body), body

    async def profile(self, person_id: str, ctx: VendorCallContext) -> CustomerProfile:
        """A customer's own details (person id = the token's user)."""
        person, _ = await self.person(person_id, ctx)
        return CustomerProfile(
            person_id=person.person_id or person_id,
            first_name=person.first_name,
            last_name=person.last_name,
            mobile_number=person.mobile_number,
            mobile_country_code=person.mobile_country_code,
            email_address=person.email_address,
            address=person.address,
            broker_id=person.broker_id,
        )

    # -- onboarding --------------------------------------------------------

    def _initial_custom_fields(self) -> dict[str, str]:
        """Every LAS field, not set. Filled in as services are paid for."""
        return {f: NOT_SET for f in all_field_ids(self._settings)}

    async def _open_wallet(self, person_id: str, ctx: VendorCallContext) -> object:
        # Canonical key; the client sends it as `programMngrId` on this path.
        payload = {
            "programManagerId": self._settings.vendor_program_manager_id,
            "personId": person_id,
            "groupId": self._settings.vendor_group_id,
            "tagId": self._settings.vendor_wallet_tag_id,
        }
        return await self._client.post(WALLET_PATH, payload, ctx)

    async def _create_login(
        self,
        person_id: str,
        first_name: str,
        last_name: str | None,
        email: str,
        ctx: VendorCallContext,
    ) -> tuple[VendorAck, str]:
        """Create the customer's IAM login with a fresh temporary password.

        userName and mobileNumber are both the mobile number, which is also
        the person id (user spec, 2026-10-27). Returns the ack and the
        password, which exists nowhere else afterwards.
        """
        s = self._settings
        password = temporary_password(s.customer_password_length)
        req = CreateUserRequest(
            user_name=person_id,
            email=email,
            first_name=first_name,
            # The vendor requires a last name; "NA" is what the spec sends.
            last_name=last_name or NOT_SET,
            group_name=s.customer_iam_group_name,
            mobile_country_code=s.customer_mobile_country_code,
            mobile_number=person_id,
            password=SecretStr(password),
        )
        # The BD's token gets 403 from the vendor's Keycloak here; see
        # app/services/admin_session.py.
        ack = await self._admin.run(
            ctx, lambda admin_ctx: self._identity.create_user(req, admin_ctx)
        )
        return VendorAck(**ack.model_dump()), password

    async def create_customer(
        self, req: CreateCustomerRequest, broker_id: str, ctx: VendorCallContext
    ) -> CreateCustomerResponse:
        """Register the person under this BD, open their wallet, then create
        their login.

        A registration failure is raised as usual. A wallet or login failure
        is not: the customer now exists, so it is reported in the response and
        the BD can retry that step with `open_wallet` or `create_login`. The
        two are independent, so the login is attempted even if the wallet
        failed.
        """
        person_id = req.mobile_number
        s = self._settings
        payload: dict[str, object] = {
            "personId": person_id,
            "brokerId": broker_id,
            "emailAddress": str(req.email_address),
            "addressStreetAddress": req.street_address,
            "addressCity": req.city,
            "addressStateProvince": req.state_province,
            "addressPostalCode": req.postal_code,
            "addressCountryCode": req.country_code.upper(),
            "mobileNumber": req.mobile_number,
            "mobileCountryCode": s.customer_mobile_country_code,
            "firstName": req.first_name,
            "settlementAccountNo": s.default_settlement_account_no,
            "settlementAccountIfscCode": s.default_settlement_ifsc_code,
            "programManagerId": s.vendor_program_manager_id,
            "groupId": s.vendor_group_id,
            "customFields": self._initial_custom_fields(),
        }
        # Optional to the vendor; null is not the same as absent.
        if req.last_name:
            payload["lastName"] = req.last_name

        body = await self._client.post(REGISTER_PATH, payload, ctx)
        result = CreateCustomerResponse(
            person_id=person_id,
            registration=VendorAck.from_vendor(body),
            wallet_opened=False,
            login_created=False,
            login_user_id=person_id,
        )

        try:
            wallet_body = await self._open_wallet(person_id, ctx)
            result.wallet_opened = True
            result.wallet = VendorAck.from_vendor(wallet_body)
        except VendorError as exc:
            if exc.http_status == 409:  # already open: the goal is met
                result.wallet_opened = True
            else:
                _log_step_failure("wallet", person_id, exc)
                result.wallet_error = StepError(**error_details(exc))

        try:
            result.login, result.temporary_password = await self._create_login(
                person_id, req.first_name, req.last_name, str(req.email_address), ctx
            )
            result.login_created = True
        except VendorError as exc:
            # A 409 means a login exists, but not with a password we know, so
            # it is reported as such rather than as created.
            _log_step_failure("login", person_id, exc)
            result.login_error = StepError(**error_details(exc))
        if result.temporary_password:
            (
                result.credentials_emailed,
                result.credentials_email_error,
            ) = await self._email_credentials(
                person_id,
                req.first_name,
                str(req.email_address),
                result.temporary_password,
            )
        return result

    async def open_wallet(
        self, person_id: str, broker_id: str, ctx: VendorCallContext
    ) -> OpenWalletResponse:
        """Open the wallet of one of this BD's customers; safe to repeat.

        Checks ownership first (CustomerNotFoundError otherwise). A 409 means
        the wallet already exists, which is success for a retry.
        """
        await self.customer(person_id, broker_id, ctx)
        try:
            body = await self._open_wallet(person_id, ctx)
        except VendorError as exc:
            if exc.http_status == 409:
                return OpenWalletResponse(person_id=person_id, already_open=True)
            raise
        return OpenWalletResponse(
            person_id=person_id, already_open=False, wallet=VendorAck.from_vendor(body)
        )

    async def create_login(
        self, person_id: str, broker_id: str, ctx: VendorCallContext
    ) -> CreateLoginResponse:
        """Create the login of one of this BD's customers; safe to repeat.

        Checks ownership first and takes the name and email from the person
        record. A 409 means the login already exists; its password is left
        alone and none is returned.
        """
        person = await self.customer(person_id, broker_id, ctx)
        if not person.first_name or not person.email_address:
            raise CustomerIncompleteError(
                "The customer has no first name or email, which a login needs."
            )
        try:
            ack, password = await self._create_login(
                person_id,
                person.first_name,
                person.last_name,
                person.email_address,
                ctx,
            )
        except VendorError as exc:
            if exc.http_status == 409:
                return CreateLoginResponse(
                    person_id=person_id, login_user_id=person_id, already_exists=True
                )
            raise
        emailed, email_error = await self._email_credentials(
            person_id, person.first_name, person.email_address, password
        )
        return CreateLoginResponse(
            person_id=person_id,
            login_user_id=person_id,
            already_exists=False,
            login=ack,
            temporary_password=password,
            credentials_emailed=emailed,
            credentials_email_error=email_error,
        )

    async def _email_credentials(
        self, person_id: str, first_name: str, email: str, password: str
    ) -> tuple[bool, str | None]:
        """Email a new login's details to the customer. Never raises: the
        login exists either way, and the BD still sees the password once."""
        if self._mailer is None or not self._mailer.configured:
            logger.warning(
                "credentials_mail_skipped person_id=%s reason=not_configured", person_id
            )
            return (
                False,
                "Email is not set up on the server, so the login details "
                "were not sent.",
            )
        mail = credentials_mail(
            to=email,
            first_name=first_name,
            user_id=person_id,
            password=password,
            sign_in_url=self._settings.dashboard_sign_in_url,
        )
        try:
            await self._mailer.send(mail)
        except (MailNotConfiguredError, MailSendError):
            return (
                False,
                "The login details could not be emailed. Share them with "
                "the customer yourself.",
            )
        logger.info("credentials_mail_sent person_id=%s", person_id)
        return True, None

    # -- editing ------------------------------------------------------------

    async def update_email(
        self,
        person_id: str,
        req: UpdateEmailRequest,
        broker_id: str,
        ctx: VendorCallContext,
    ) -> UpdateCustomerResponse:
        """Change the email on the person record of one of this BD's customers.

        Only the person record: the customer's login (IAM user) keeps the
        email it was created with.
        """
        await self.owned_person(person_id, broker_id, ctx)
        payload = {
            "programManagerId": self._settings.vendor_program_manager_id,
            "personId": person_id,
            "emailAddress": str(req.email_address),
        }
        body = await self._client.put(EMAIL_PATH, payload, ctx)
        return UpdateCustomerResponse(
            person_id=person_id, updated=VendorAck.from_vendor(body)
        )

    async def update_address(
        self,
        person_id: str,
        req: UpdateAddressRequest,
        broker_id: str,
        ctx: VendorCallContext,
    ) -> UpdateCustomerResponse:
        """Replace the postal address of one of this BD's customers."""
        await self.owned_person(person_id, broker_id, ctx)
        payload = {
            "programManagerId": self._settings.vendor_program_manager_id,
            "personId": person_id,
            "addressStreetAddress": req.street_address,
            "addressCity": req.city,
            "addressStateProvince": req.state_province,
            "addressPostalCode": req.postal_code,
            "addressCountryCode": req.country_code.upper(),
        }
        body = await self._client.put(ADDRESS_PATH, payload, ctx)
        return UpdateCustomerResponse(
            person_id=person_id, updated=VendorAck.from_vendor(body)
        )


def _log_step_failure(step: str, person_id: str, exc: VendorError) -> None:
    logger.warning(
        "customer_registered_without_%s person_id=%s vendor_status=%s code=%r txn=%s",
        step,
        person_id,
        exc.http_status,
        exc.error_code,
        exc.transaction_id,
    )
