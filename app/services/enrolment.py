"""Opting a customer into services, taking payment, and storing documents.

All state is person custom fields, one set per service; see
app/services/service_fields.py for the fields and the customer-level summary,
which every write here recomputes.

Pay now: the customer pays by QR outside the vendor; the BD confirms receipt
and we record it. No money moves through the vendor.

Pay later: with both consents, the amount moves from the pool system account
to the main account wallet (PUT /transfer/systemAccountPayment). The vendor
documents no idempotency key and the transfer names no customer, so a double
payment could not be detected afterwards. Hence the order:

    1. refuse while any service's payment is "Processing"
    2. mark the new services "Processing" (with their mode)
    3. transfer -- never retried
    4. mark them "Paid", with the transfer's transaction id

If the vendor refuses the transfer (4xx), nothing moved: step 2 is undone and
the BD may try again. If the outcome is unknown (5xx, timeout), "Processing"
stays and blocks any further payment until support reconciles it.

Services can be added after the first payment (Edit customer): same flow,
only for services not yet opted, without the joining fee.

A signed-in customer can opt in too (`request`). Pay later then waits for
their BD to authorize -- stand guarantor -- and the transfer runs at that
point (`authorize`); the BD may `decline`. Waiting services are "Awaiting BD".
"""

from collections.abc import Mapping

from app.clients.vendor_client import VendorCallContext, VendorClient
from app.core.config import ServiceConfig, Settings
from app.core.exceptions import VendorError
from app.core.logging import get_logger
from app.schemas.common import VendorAck
from app.schemas.custom_field import UpdateCustomFieldsRequest
from app.schemas.enrolment import (
    Catalog,
    CatalogService,
    CustomerService,
    CustomerServices,
    DocumentUploadResponse,
    PaymentMode,
    PaymentRequest,
    PaymentResponse,
    ServiceRequest,
    ServiceRequestResponse,
)
from app.services.bd import BdService
from app.services.custom_field import CustomFieldService
from app.services.documents import (
    StoredFile,
    custom_field_values,
    decode_file,
    encode_file,
    file_info,
)
from app.services.service_fields import (
    DOC_STATUS_FIELD,
    NOT_SET,
    PAYMENT_MODE_FIELD,
    PAYMENT_STATUS_FIELD,
    ServiceFields,
    field_value,
    in_house,
    is_opted,
    is_requested,
    same,
    summary,
)

logger = get_logger(__name__)

TRANSFER_PATH = "/transfer/systemAccountPayment"


class UnknownServiceError(LookupError):
    """Not an in-house service in the catalogue."""


class PaymentNotAllowedError(ValueError):
    """The request is valid, but the customer's state forbids it (409)."""


class PaymentRequestError(ValueError):
    """The request itself is wrong, e.g. missing consent (422)."""


class DocumentNotAllowedError(ValueError):
    """Upload refused: not paid, not opted, or already approved (409)."""


class DocumentNotStoredError(LookupError):
    pass


class PaymentOutcomeUnknownError(Exception):
    """The transfer may or may not have happened. Never retry automatically."""

    def __init__(self, cause: VendorError) -> None:
        self.cause = cause
        super().__init__(str(cause))


class EnrolmentService:
    def __init__(self, client: VendorClient, settings: Settings, bd: BdService) -> None:
        self._client = client
        self._settings = settings
        self._bd = bd
        self._fields = CustomFieldService(client, settings)

    # -- catalogue ---------------------------------------------------------

    def catalog(self) -> Catalog:
        return Catalog(
            joining_fee=self._settings.joining_fee,
            services=[
                CatalogService(**s.model_dump(include=set(CatalogService.model_fields)))
                for s in self._settings.services
            ],
        )

    def _service(self, code: str) -> ServiceConfig:
        for s in in_house(self._settings):
            if s.code == code:
                return s
        raise UnknownServiceError(code)

    # -- state -------------------------------------------------------------

    async def _values(
        self, person_id: str, broker_id: str | None, ctx: VendorCallContext
    ) -> dict[str, str]:
        """The customer's fields. `broker_id` None: the customer themselves
        (the caller has already made sure person_id is the token's user)."""
        if broker_id is None:
            _, body = await self._bd.person(person_id, ctx)
        else:
            _, body = await self._bd.owned_person(person_id, broker_id, ctx)
        return custom_field_values(body)

    def _locked(self, status: str | None) -> bool:
        """Approved or reported on: the document can no longer change."""
        s = self._settings
        return same(status, s.service_approved_value) or same(
            status, s.service_report_ready_value
        )

    def _view(self, values: Mapping[str, str], s: ServiceConfig) -> CustomerService:
        f = ServiceFields.of(s)
        status = field_value(values.get(f.status))
        payment = field_value(values.get(f.payment))
        doc = field_value(values.get(f.doc))
        paid = same(payment, self._settings.payment_status_paid)
        opted = is_opted(status, self._settings)
        return CustomerService(
            code=s.code,
            name=s.name,
            fee=s.fee,
            opted=opted,
            requested=is_requested(status, self._settings),
            status=status,
            payment_status=payment,
            payment_mode=field_value(values.get(f.pay_mode)),
            transaction_id=field_value(values.get(f.txn_id)),
            document=file_info(doc, s.code.lower()) if doc else None,
            can_upload=opted and paid and not self._locked(status),
        )

    def _in_progress(self, values: Mapping[str, str]) -> bool:
        return any(
            same(
                field_value(values.get(ServiceFields.of(s).payment)),
                self._settings.payment_status_processing,
            )
            for s in in_house(self._settings)
        )

    def _first_payment(self, values: Mapping[str, str]) -> bool:
        return not any(
            field_value(values.get(ServiceFields.of(s).payment)) is not None
            for s in in_house(self._settings)
        )

    async def customer_services(
        self, person_id: str, broker_id: str | None, ctx: VendorCallContext
    ) -> CustomerServices:
        values = await self._values(person_id, broker_id, ctx)
        return CustomerServices(
            person_id=person_id,
            services=[self._view(values, s) for s in in_house(self._settings)],
            payment_status=field_value(values.get(PAYMENT_STATUS_FIELD)),
            payment_mode=field_value(values.get(PAYMENT_MODE_FIELD)),
            doc_status=field_value(values.get(DOC_STATUS_FIELD)),
            first_payment=self._first_payment(values),
            payment_in_progress=self._in_progress(values),
        )

    # -- writing -----------------------------------------------------------

    async def _write(
        self,
        person_id: str,
        values: Mapping[str, str],
        changes: dict[str, str],
        ctx: VendorCallContext,
    ) -> dict[str, str]:
        """Write `changes` plus the recomputed summary, in one vendor call.

        Returns the customer's values as they now are.
        """
        after = {**values, **changes}
        fields = {**changes, **summary(after, self._settings)}
        await self._fields.update_values(
            person_id, UpdateCustomFieldsRequest(custom_fields=fields), ctx
        )
        return {**after, **fields}

    def _location(self, ctx: VendorCallContext) -> dict[str, float]:
        s = self._settings

        def number(value: str, fallback: str) -> float:
            try:
                return float(value)
            except ValueError:
                return float(fallback)

        return {
            "latitude": number(ctx.latitude, s.default_geo_latitude),
            "longitude": number(ctx.longitude, s.default_geo_longitude),
        }

    async def _transfer(self, amount: int, ctx: VendorCallContext) -> VendorAck:
        s = self._settings
        payload = {
            "programManagerId": s.vendor_program_manager_id,
            "transferTypeId": s.vendor_pool_transfer_type_id,
            "debitFrom": s.vendor_pool_debit_from,
            "creditTo": s.vendor_main_account_id,
            "creditToTagId": s.vendor_main_account_tag_id,
            "creditToGroupId": s.vendor_main_account_group_id,
            "amount": amount,
            "customFields": {},
            "location": self._location(ctx),
        }
        body = await self._client.put(TRANSFER_PATH, payload, ctx)
        return VendorAck.from_vendor(body)

    # -- payment -----------------------------------------------------------

    async def pay(
        self,
        person_id: str,
        req: PaymentRequest,
        broker_id: str,
        ctx: VendorCallContext,
    ) -> PaymentResponse:
        codes = list(dict.fromkeys(req.services))  # de-duplicated, in order
        services = [self._service(c) for c in codes]  # UnknownServiceError
        if req.mode == "pay_later" and not (req.bd_consent and req.customer_consent):
            raise PaymentRequestError(
                "Pay later needs the consent of both the BD and the customer."
            )

        values = await self._values(person_id, broker_id, ctx)
        self._check_free(values, services)
        return await self._settle(person_id, values, services, req.mode, ctx)

    def _check_free(
        self, values: Mapping[str, str], services: list[ServiceConfig]
    ) -> None:
        """No payment in flight, and none of `services` taken or requested."""
        if self._in_progress(values):
            raise PaymentNotAllowedError(
                "A payment for this customer is still being processed. "
                "Contact support before trying again."
            )
        statuses = {x.name: values.get(ServiceFields.of(x).status) for x in services}
        requested = [n for n, v in statuses.items() if is_requested(v, self._settings)]
        if requested:
            raise PaymentNotAllowedError(
                f"Waiting for the BD to authorize: {', '.join(requested)}."
            )
        taken = [n for n, v in statuses.items() if is_opted(v, self._settings)]
        if taken:
            raise PaymentNotAllowedError(f"Already opted: {', '.join(taken)}.")

    async def _settle(
        self,
        person_id: str,
        values: Mapping[str, str],
        services: list[ServiceConfig],
        mode: PaymentMode,
        ctx: VendorCallContext,
    ) -> PaymentResponse:
        """Opt `services` in and record or make the payment (see module doc).

        The caller has checked consent and that the services are free (or,
        for a customer's request, that they are requested).
        """
        s = self._settings
        codes = [x.code for x in services]
        first = self._first_payment(values)
        joining_fee = s.joining_fee if first else 0
        amount = sum(x.fee for x in services) + joining_fee
        fields = [ServiceFields.of(x) for x in services]
        mode_value = (
            s.payment_mode_pay_now if mode == "pay_now" else s.payment_mode_pay_later
        )

        def mark(payment: str) -> dict[str, str]:
            changes: dict[str, str] = {}
            for f in fields:
                changes[f.status] = s.service_opted_value
                changes[f.payment] = payment
                changes[f.pay_mode] = mode_value
            return changes

        def response(**extra: object) -> PaymentResponse:
            return PaymentResponse(
                person_id=person_id,
                mode=mode,
                services=codes,
                amount=amount,
                joining_fee=joining_fee,
                **extra,
            )

        if mode == "pay_now":
            # The BD has confirmed the QR payment was received.
            await self._write(person_id, values, mark(s.payment_status_paid), ctx)
            logger.info(
                "payment_recorded person_id=%s mode=pay_now amount=%s services=%s",
                person_id,
                amount,
                codes,
            )
            return response(payment_status=s.payment_status_paid)

        # Pay later. Step 2: claim the services before any money moves.
        claimed = await self._write(
            person_id, values, mark(s.payment_status_processing), ctx
        )
        # Step 3: the transfer. A write: the client never retries it.
        try:
            transfer = await self._transfer(amount, ctx)
        except VendorError as exc:
            if 400 <= exc.http_status < 500:
                await self._release(person_id, values, fields, amount, exc, ctx)
                if exc.http_status == 401:
                    # The BD's token just worked for step 2, so this is the
                    # transfer's permissions, not an expired session. A 401
                    # would sign the BD out.
                    raise VendorError(
                        502,
                        error_code="TRANSFER_NOT_PERMITTED",
                        error_message="The vendor refused the transfer (401)",
                        transaction_id=exc.transaction_id,
                        correlation_id=exc.correlation_id,
                        method=exc.method,
                        path=exc.path,
                    ) from exc
                raise
            logger.error(
                "pool_transfer_outcome_unknown person_id=%s amount=%s status=%s "
                "txn=%s services=%s -- left as %r; reconcile with the vendor "
                "before any retry",
                person_id,
                amount,
                exc.http_status,
                exc.transaction_id,
                codes,
                s.payment_status_processing,
            )
            raise PaymentOutcomeUnknownError(exc) from exc

        logger.info(
            "pool_transfer_done person_id=%s amount=%s services=%s txn=%s corr=%s",
            person_id,
            amount,
            codes,
            transfer.transaction_id,
            transfer.correlation_id,
        )
        # Step 4.
        done: dict[str, str] = {}
        for f in fields:
            done[f.payment] = s.payment_status_paid
            if transfer.transaction_id:
                done[f.txn_id] = transfer.transaction_id
        try:
            await self._write(person_id, claimed, done, ctx)
        except VendorError as exc:
            logger.error(
                "payment_status_not_recorded person_id=%s transfer_txn=%s "
                "status=%s -- money moved; write %r by hand",
                person_id,
                transfer.transaction_id,
                exc.http_status,
                done,
            )
            return response(
                payment_status=s.payment_status_processing,
                transfer=transfer,
                status_recorded=False,
            )
        return response(payment_status=s.payment_status_paid, transfer=transfer)

    async def _release(
        self,
        person_id: str,
        values: Mapping[str, str],
        fields: list[ServiceFields],
        amount: int,
        exc: VendorError,
        ctx: VendorCallContext,
    ) -> None:
        """Undo step 2 after a refused transfer: nothing moved."""
        logger.warning(
            "pool_transfer_refused person_id=%s amount=%s status=%s code=%r",
            person_id,
            amount,
            exc.http_status,
            exc.error_code,
        )
        # Back to what they were: NA, or the customer's pending request.
        reset = {
            i: values.get(i) or NOT_SET
            for f in fields
            for i in (f.status, f.payment, f.pay_mode)
        }
        try:
            await self._write(person_id, values, reset, ctx)
        except VendorError:
            logger.error(
                "payment_claim_not_released person_id=%s -- no money moved; "
                "write %r by hand",
                person_id,
                reset,
            )

    # -- a customer's own requests ----------------------------------------

    async def request(
        self, person_id: str, req: ServiceRequest, ctx: VendorCallContext
    ) -> ServiceRequestResponse:
        """A signed-in customer opts into services themselves.

        Pay later always waits for their BD to authorize (stand guarantor);
        the pool transfer runs then. Pay now is recorded at once, unless
        `customer_pay_now_needs_confirmation` (while the QR is a placeholder,
        nothing proves the customer paid), in which case it waits for the BD
        to confirm receipt. Waiting services are "Awaiting BD" and are not
        opted until then.
        """
        s = self._settings
        codes = list(dict.fromkeys(req.services))
        services = [self._service(c) for c in codes]  # UnknownServiceError
        if req.mode == "pay_later" and not req.customer_consent:
            raise PaymentRequestError("Pay later needs your consent to pay later.")

        values = await self._values(person_id, None, ctx)
        self._check_free(values, services)
        first = self._first_payment(values)
        amount = sum(x.fee for x in services) + (s.joining_fee if first else 0)

        if req.mode == "pay_now" and not s.customer_pay_now_needs_confirmation:
            payment = await self._settle(person_id, values, services, "pay_now", ctx)
            return ServiceRequestResponse(
                person_id=person_id,
                mode=req.mode,
                services=codes,
                amount=payment.amount,
                awaiting_bd=False,
                payment=payment,
            )

        mode_value = (
            s.payment_mode_pay_now
            if req.mode == "pay_now"
            else s.payment_mode_pay_later
        )
        changes: dict[str, str] = {}
        for f in (ServiceFields.of(x) for x in services):
            changes[f.status] = s.service_requested_value
            changes[f.pay_mode] = mode_value
        await self._write(person_id, values, changes, ctx)
        logger.info(
            "service_requested person_id=%s mode=%s services=%s amount=%s",
            person_id,
            req.mode,
            codes,
            amount,
        )
        return ServiceRequestResponse(
            person_id=person_id,
            mode=req.mode,
            services=codes,
            amount=amount,
            awaiting_bd=True,
        )

    async def _requested(
        self, person_id: str, code: str, broker_id: str, ctx: VendorCallContext
    ) -> tuple[ServiceConfig, dict[str, str], PaymentMode]:
        """One of this BD's customer's pending requests, and its mode."""
        service = self._service(code)
        values = await self._values(person_id, broker_id, ctx)
        f = ServiceFields.of(service)
        if not is_requested(values.get(f.status), self._settings):
            raise PaymentNotAllowedError(
                f"{service.name} has no request waiting for authorization."
            )
        mode: PaymentMode = (
            "pay_later"
            if same(
                field_value(values.get(f.pay_mode)),
                self._settings.payment_mode_pay_later,
            )
            else "pay_now"
        )
        return service, values, mode

    async def authorize(
        self,
        person_id: str,
        code: str,
        bd_consent: bool,
        broker_id: str,
        ctx: VendorCallContext,
    ) -> PaymentResponse:
        """The BD authorizes a customer's request.

        Pay later: the BD stands guarantor (`bd_consent`) and the pool
        transfer runs now. Pay now: the BD confirms the QR payment arrived.
        """
        service, values, mode = await self._requested(person_id, code, broker_id, ctx)
        if mode == "pay_later" and not bd_consent:
            raise PaymentRequestError(
                "Authorizing pay later means standing guarantor: give your consent."
            )
        if self._in_progress(values):
            raise PaymentNotAllowedError(
                "A payment for this customer is still being processed. "
                "Contact support before trying again."
            )
        logger.info(
            "request_authorized person_id=%s service=%s mode=%s broker_id=%s",
            person_id,
            code,
            mode,
            broker_id,
        )
        return await self._settle(person_id, values, [service], mode, ctx)

    async def decline(
        self, person_id: str, code: str, broker_id: str, ctx: VendorCallContext
    ) -> CustomerServices:
        """The BD declines a customer's request: the service is free again."""
        service, values, mode = await self._requested(person_id, code, broker_id, ctx)
        f = ServiceFields.of(service)
        await self._write(
            person_id, values, {f.status: NOT_SET, f.pay_mode: NOT_SET}, ctx
        )
        logger.info(
            "request_declined person_id=%s service=%s mode=%s broker_id=%s",
            person_id,
            code,
            mode,
            broker_id,
        )
        return await self.customer_services(person_id, broker_id, ctx)

    # -- documents ---------------------------------------------------------

    async def upload_document(
        self,
        person_id: str,
        code: str,
        file: StoredFile,
        broker_id: str | None,
        ctx: VendorCallContext,
    ) -> DocumentUploadResponse:
        """Store the service's document; its status becomes "Docs uploaded".

        Allowed once that service is paid, until it is approved. Replaces an
        earlier upload (e.g. after a rejection).
        """
        service = self._service(code)
        f = ServiceFields.of(service)
        values = await self._values(person_id, broker_id, ctx)
        view = self._view(values, service)
        if not view.opted:
            raise DocumentNotAllowedError(f"{service.name} was not opted for.")
        if not same(view.payment_status, self._settings.payment_status_paid):
            raise DocumentNotAllowedError(
                f"{service.name} can take documents once it is paid."
            )
        if self._locked(view.status):
            raise DocumentNotAllowedError(
                f"{service.name} is already {view.status}; its document is locked."
            )

        stored = encode_file(file)
        after = await self._write(
            person_id,
            values,
            {f.doc: stored, f.status: self._settings.service_docs_uploaded_value},
            ctx,
        )
        doc_status = after[DOC_STATUS_FIELD]
        logger.info(
            "document_stored person_id=%s service=%s bytes=%s doc_status=%r",
            person_id,
            code,
            len(file.data),
            doc_status,
        )
        info = file_info(stored, code.lower())
        assert info is not None  # just encoded
        return DocumentUploadResponse(
            person_id=person_id, code=code, document=info, doc_status=doc_status
        )

    async def document(
        self, person_id: str, code: str, broker_id: str | None, ctx: VendorCallContext
    ) -> StoredFile:
        service = self._service(code)
        values = await self._values(person_id, broker_id, ctx)
        raw = field_value(values.get(ServiceFields.of(service).doc))
        stored = decode_file(raw, code.lower()) if raw else None
        if stored is None:
            raise DocumentNotStoredError(code)
        return stored
