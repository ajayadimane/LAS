"""Turns stored intake events into registered customers (Phase 1).

One event = one BD email. For each customer it names we open a case and walk
it through:

    NEW -> NEEDS_INFO (ask the BD for what is missing; reply merges, 24 h
           reminder, closed after 72 h: EXPIRED)
        -> REGISTERING (person + wallet + login, BdService.create_customer)
        -> AWAITING_PAYMENT (mail: registered, choose Pay now / Pay later)
    or FAILED (the vendor refused; BD told why)
    or NEEDS_ATTENTION (outcome unknown, e.g. a timeout; never retried)

Payment replies, document upload and follow-up mails (status, more services)
are later phases: such mails are recorded and IGNORED with a reason for now.

Safety rules carried over from the dashboard: registration is never retried
blindly (a vendor 409 is checked against this BD's customers instead), mail
from senders not in INTAKE_BD_SENDERS or without DKIM/DMARC pass is ignored,
and nothing an email says is treated as an instruction beyond the fields
extracted from it.
"""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.clients.vendor_client import VendorClient
from app.core.config import Settings
from app.core.exceptions import VendorError, error_details
from app.core.logging import get_logger
from app.schemas.enrolment import PaymentRequest
from app.schemas.intake import IntakeEvent
from app.services.admin_session import AdminSession
from app.services.bd import BdService, CustomerNotFoundError
from app.services.documents import (
    StoredFile,
    custom_field_values,
    document_problem,
    encode_file,
)
from app.services.dpr_agentic.document_queue import _project_context, queue_analysis
from app.services.dpr_agentic.report_store import get_report, get_report_pdf
from app.services.dues import is_settled, outstanding, requeue_report_sync
from app.services.dues import settle as dues_settle
from app.services.enrolment import (
    DocumentNotAllowedError,
    EnrolmentService,
    PaymentNotAllowedError,
    PaymentOutcomeUnknownError,
    PaymentRequestError,
    UnknownServiceError,
)
from app.services.intake import cases as case_store
from app.services.intake import mail as templates
from app.services.intake import reports as report_sync
from app.services.intake import store
from app.services.intake.case_data import CaseData, CaseDocument, normalise_mobile
from app.services.intake.cases import Case
from app.services.intake.payment_reply import customer_paid, payment_choice
from app.services.intake.vendor_session import (
    IntakeNotConfiguredError,
    IntakeVendorSession,
)
from app.services.mailer import Mail, Mailer, MailNotConfiguredError, MailSendError
from app.services.service_fields import ServiceFields, field_value, same

logger = get_logger(__name__)

NEW_CUSTOMER_INTENTS = {"NEW_CUSTOMER", "MULTI_CUSTOMER"}
# Vendor codes meaning "this customer already exists" (besides HTTP 409).
ALREADY_REGISTERED_CODES = {"MOBILE_NUMBER_ALREADY_EXISTS"}
# A BD's reply to a case in these states is merged into it.
REOPENABLE_STATES = {"NEEDS_INFO", "FAILED"}


class IntakeProcessor:
    def __init__(
        self,
        engine,
        settings: Settings,
        client: VendorClient,
        admin: AdminSession,
        mailer: Mailer,
        dpr_engine=None,
    ) -> None:
        assert settings.intake_encryption_key is not None
        self._engine = engine
        self._settings = settings
        self._key = settings.intake_encryption_key.get_secret_value().encode()
        self._mailer = mailer
        self._bd = BdService(client, settings, admin, mailer)
        self._enrolment = EnrolmentService(
            client, settings, self._bd, dues_engine=dpr_engine or engine
        )
        # The DPR job registry (app/services/dpr_agentic/jobs.py); None = no
        # analysis is queued for emailed documents.
        self._dpr_engine = dpr_engine
        self._session = IntakeVendorSession(client, settings)

    async def _db(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, self._engine, *args, **kwargs)

    # -- the loop ----------------------------------------------------------

    async def tick(self) -> dict[str, int]:
        """One pass: process due events, then reminders and expiries."""
        counts: dict[str, int] = {}
        for event_id in await self._db(store.claim_events):
            try:
                status = await self.process_event(event_id)
            except Exception as exc:
                logger.exception("intake_event_failed intake_event_id=%s", event_id)
                status = await self._db(
                    store.release_event, event_id, f"{type(exc).__name__}"
                )
            counts[status] = counts.get(status, 0) + 1
        for case_id, what in await self._db(
            case_store.due_info_cases,
            reminder_after_hours=self._settings.intake_info_reminder_hours,
        ):
            await self._follow_up(case_id, what)
            counts[what] = counts.get(what, 0) + 1
        if self._dpr_engine is not None:
            for outcome in await self._sync_reports():
                counts[outcome] = counts.get(outcome, 0) + 1
        return counts

    # -- finished DPR reports -> wallet + BD ----------------------------------

    async def _sync_reports(self) -> list[str]:
        """Write each newly finished report back to the wallet once: status
        "Report ready", the PDF in its report field, then email the BD."""
        engine = self._dpr_engine
        await asyncio.to_thread(
            report_sync.discover,
            engine,
            self._settings.intake_report_sync_lookback_hours,
        )
        outcomes: list[str] = []
        for item in await asyncio.to_thread(report_sync.claim_due, engine):
            try:
                outcome, emailed = await self._sync_report(item)
            except Exception as exc:
                logger.exception("report_sync_failed run_id=%s", item.run_id)
                await asyncio.to_thread(
                    report_sync.finish,
                    engine,
                    item.run_id,
                    "FAILED",
                    error=type(exc).__name__,
                )
                outcomes.append("REPORT_SYNC_FAILED")
                continue
            await asyncio.to_thread(
                report_sync.finish,
                engine,
                item.run_id,
                "DONE",
                outcome=outcome,
                bd_emailed=emailed,
            )
            outcomes.append(f"REPORT_{outcome}")
        return outcomes

    async def _sync_report(self, item: report_sync.ReportToSync) -> tuple[str, bool]:
        parsed = item.person_and_code
        if parsed is None:
            return "NOT_A_PERSON_REPORT", False
        person_id, code = parsed
        try:
            svc = self._enrolment._service(code)  # noqa: SLF001
        except UnknownServiceError:
            return "UNKNOWN_SERVICE", False
        if not svc.reports:
            return "NOT_A_REPORT_SERVICE", False
        fields = ServiceFields.of(svc)
        engine = self._dpr_engine
        pdf = await asyncio.to_thread(
            get_report_pdf, engine, item.source_reference, version=item.version
        )
        report = await asyncio.to_thread(
            get_report, engine, item.source_reference, version=item.version
        )
        ctx = await self._session.context()
        person, body = await self._bd.person(person_id, ctx)
        values = custom_field_values(body)
        who = (
            " ".join(p for p in (person.first_name, person.last_name) if p) or person_id
        )
        changes = {fields.status: self._settings.service_report_ready_value}
        if await self._payment_due(person_id, code, values, fields):
            # Pay later, customer not paid yet: the report is locked, so its
            # PDF is held back from the wallet; re-synced once settled.
            await self._enrolment._write(person_id, values, changes, ctx)  # noqa: SLF001
            owed = await outstanding(self._enrolment, engine, person_id, None, ctx)
            amount = next((i.amount for i in owed if i.code == code), svc.fee)
            emailed = await self._email_bd(
                person_id,
                person.broker_id,
                lambda ref: templates.report_locked(
                    ref,
                    who,
                    person_id,
                    self._settings,
                    service_name=svc.name,
                    amount=amount,
                ),
            )
            if person.email_address:
                await self._send_plain(
                    person.email_address,
                    templates.report_due_customer(
                        who, svc.name, amount, self._settings
                    ),
                )
            logger.info(
                "report_locked_customer_due person_id=%s code=%s amount=%s",
                person_id,
                code,
                amount,
            )
            return "LOCKED_DUE", emailed
        outcome = "STATUS_ONLY"
        # A person only has the custom fields that existed when they were
        # registered (vendor gap): no report field = status only.
        if pdf and fields.report and fields.report in values:
            changes[fields.report] = encode_file(
                StoredFile(
                    name=f"{svc.code}-report-{person_id}-v{item.version}.pdf",
                    content_type="application/pdf",
                    data=pdf,
                )
            )
            outcome = "REPORT_STORED"
        try:
            await self._enrolment._write(person_id, values, changes, ctx)  # noqa: SLF001
        except VendorError as exc:
            if outcome != "REPORT_STORED" or not 400 <= exc.http_status < 500:
                raise
            # The PDF was refused (e.g. too large): keep the status at least.
            logger.warning(
                "report_pdf_refused person_id=%s status=%s code=%s",
                person_id,
                exc.http_status,
                exc.error_code,
            )
            changes.pop(fields.report)
            await self._enrolment._write(person_id, values, changes, ctx)  # noqa: SLF001
            outcome = "STATUS_ONLY"
        logger.info(
            "report_synced person_id=%s code=%s version=%s outcome=%s",
            person_id,
            code,
            item.version,
            outcome,
        )
        emailed = await self._email_bd(
            person_id,
            person.broker_id,
            lambda ref: templates.report_ready(
                ref,
                who,
                person_id,
                service_name=svc.name,
                version=item.version,
                report=report,
                outcome=outcome,
            ),
        )
        return outcome, emailed

    async def _payment_due(self, person_id: str, code: str, values, fields) -> bool:
        """Pay later and the customer has not paid yet (app/services/dues.py)."""
        mode = field_value(values.get(fields.pay_mode))
        if not same(mode, self._settings.payment_mode_pay_later):
            return False
        engine = self._dpr_engine or self._engine
        return not await asyncio.to_thread(is_settled, engine, person_id, code)

    async def _send_plain(self, to: str, mail: Mail) -> bool:
        try:
            await self._mailer.send(replace(mail, to=to))
        except (MailNotConfiguredError, MailSendError):
            return False
        return True

    async def _email_bd(self, person_id: str, broker_id, build) -> bool:
        """`build(ref)` -> Mail. In the customer's intake thread when they
        came by email; else to the BD's address from INTAKE_BD_SENDERS.
        False = no address known (or the send failed)."""
        case_id = await asyncio.to_thread(
            report_sync.latest_case_for_person, self._engine, person_id
        )
        case = (
            await self._db(case_store.load_case, case_id, self._key)
            if case_id
            else None
        )
        if case is not None:
            await self._mail(case, build(case.ref))
            return True
        address = next(
            (
                email
                for email, bd in self._settings.intake_bd_senders.items()
                if broker_id and bd.casefold() == broker_id.casefold()
            ),
            None,
        )
        if address is None:
            return False
        return await self._send_plain(address, build(None))

    async def process_event(self, intake_event_id: str) -> str:
        event = await self._db(store.load_event, intake_event_id, self._key)
        if event is None:
            return "MISSING"
        status, note = await self._handle(intake_event_id, event)
        await self._db(store.finish_event, intake_event_id, status, note)
        logger.info(
            "intake_event_done intake_event_id=%s status=%s note=%s",
            intake_event_id,
            status,
            note,
        )
        return status

    # -- one event ---------------------------------------------------------

    def _authenticated(self, event: IntakeEvent) -> bool:
        auth = event.sender.auth
        return "pass" in {(auth.dkim or "").lower(), (auth.dmarc or "").lower()}

    async def _handle(
        self, intake_event_id: str, event: IntakeEvent
    ) -> tuple[str, str]:
        sender = event.sender.email.strip().lower()
        # Never answer an address that may be forged: no reply of any kind
        # without DKIM/DMARC pass.
        if not self._authenticated(event):
            return "IGNORED", "SENDER_NOT_AUTHENTICATED"
        bd_id = self._settings.intake_bd_senders.get(sender)
        if bd_id is None:
            return await self._not_registered(sender, event)
        if event.injection_suspected:
            return "IGNORED", "INJECTION_SUSPECTED"

        thread_ids = [
            m for m in [event.thread.in_reply_to, *event.thread.references] if m
        ]
        case = await self._db(
            case_store.find_thread_case,
            message_ids=thread_ids,
            ref=case_store.ref_in_subject(event.thread.subject),
            bd_id=bd_id,
            key=self._key,
        )
        if case is not None:
            return await self._reply(case, intake_event_id, event)

        actions = [a for a in event.actions if a.intent in NEW_CUSTOMER_INTENTS]
        customers = [(a, c) for a in actions for c in a.customers]
        if not customers:
            return await self._existing_customer(bd_id, sender, intake_event_id, event)
        limit = self._settings.intake_max_customers_per_mail
        for index, (action, mention) in enumerate(customers[:limit]):
            data = CaseData()
            data.merge_customer(mention, action.services, self._settings)
            # One customer: every attachment is theirs. Several: only the
            # files the extraction tied to that customer's request.
            wanted = (
                set(action.attachment_ids)
                if len(customers) > 1
                else {a.attachment_id for a in event.attachments}
            )
            data.add_documents(
                intake_event_id,
                [a for a in event.attachments if a.attachment_id in wanted],
            )
            data.add_refused(event.rejected_attachments)
            case = await self._db(
                case_store.open_case,
                origin_event_id=intake_event_id,
                customer_index=index,
                bd_id=bd_id,
                bd_email=sender,
                data=data,
                key=self._key,
            )
            if case.state == "NEW":
                await self._advance(case, reply_to=event.event_id)
            elif case.state == "REGISTERING":
                # A crash mid-registration: try again. If the first attempt
                # did go through, the vendor's 409 sends it down the
                # already-registered path instead of registering twice.
                await self._register(case, reply_to=event.event_id)
        note = None if len(customers) <= limit else f"ONLY_FIRST_{limit}_CUSTOMERS"
        return "PROCESSED", note or f"CASES={min(len(customers), limit)}"

    async def _not_registered(self, sender: str, event: IntakeEvent) -> tuple[str, str]:
        """Tell an unknown (but authenticated) sender to ask the LAS Admin.
        At most once per sender per day, so a mail loop or a stream of
        emails never turns into a stream of replies."""
        if await self._db(store.replied_to_unknown_sender_recently, sender):
            return "IGNORED", "UNKNOWN_SENDER"
        mail = replace(
            templates.not_registered(event.thread.subject, self._settings),
            to=sender,
            in_reply_to=event.event_id,
            references=tuple(event.thread.references[-10:]),
        )
        try:
            await self._mailer.send(mail)
        except (MailNotConfiguredError, MailSendError) as exc:
            logger.warning(
                "intake_mail_not_sent unknown_sender error=%s", type(exc).__name__
            )
            return "IGNORED", "UNKNOWN_SENDER"
        logger.info(
            "intake_unknown_sender_replied domain=%s", sender.rsplit("@", 1)[-1]
        )
        return "IGNORED", store.UNKNOWN_SENDER_REPLIED

    async def _customer_paid(self, case: Case, event: IntakeEvent) -> tuple[str, str]:
        """The BD confirms a Pay later customer paid: unlock their reports and
        write the held-back PDFs to the wallet on the next pass."""
        engine = self._dpr_engine or self._engine
        ctx = await self._session.context()
        owed = await outstanding(
            self._enrolment, engine, case.person_id, case.bd_id, ctx
        )
        settled = await asyncio.to_thread(
            dues_settle, engine, case.person_id, [i.code for i in owed], case.bd_id
        )
        if settled:
            await asyncio.to_thread(
                requeue_report_sync, engine, case.person_id, settled
            )
        logger.info(
            "customer_paid_confirmed case=%s person_id=%s settled=%s",
            case.ref,
            case.person_id,
            settled,
        )
        await self._mail(
            case,
            templates.customer_paid_recorded(
                case.ref, case.data.name or case.person_id, settled
            ),
            reply_to=event.event_id,
        )
        return "PROCESSED", f"CUSTOMER_PAID settled={len(settled)}"

    async def _send_direct(self, to: str, mail: Mail, event: IntakeEvent) -> None:
        """A reply outside any case, threaded on the BD's own mail."""
        mail = replace(
            mail,
            to=to,
            in_reply_to=event.event_id,
            references=tuple(event.thread.references[-10:]),
        )
        try:
            await self._mailer.send(mail)
        except (MailNotConfiguredError, MailSendError) as exc:
            logger.warning("intake_mail_not_sent direct error=%s", type(exc).__name__)

    async def _existing_customer(
        self,
        bd_id: str,
        bd_email: str,
        intake_event_id: str,
        event: IntakeEvent,
        *,
        case: Case | None = None,
    ) -> tuple[str, str]:
        """A mail about an existing customer (Phase 3): documents, more
        services, or a status question. The customer is the finished case's,
        else the first grounded mobile the mail names; always this BD's own."""
        intents = {a.intent for a in event.actions}
        mention = next(
            (
                (a, c)
                for a in event.actions
                for c in a.customers
                if normalise_mobile(c.mobile)
            ),
            None,
        )
        person_id = (
            case.person_id
            if case
            else (normalise_mobile(mention[1].mobile) if mention else None)
        )
        requested: list[str] = []
        if "SELECT_SERVICES" in intents:
            for a in event.actions:
                requested += a.services + [x for c in a.customers for x in c.services]
        actionable = bool(
            event.attachments
            or event.rejected_attachments
            or requested
            or intents & {"STATUS_QUERY", "SUBMIT_DOCUMENT", "SELECT_SERVICES"}
        )

        async def reply(mail: Mail) -> None:
            if case is not None:
                await self._mail(case, mail, reply_to=event.event_id)
            else:
                await self._send_direct(bd_email, mail, event)

        if person_id is None:
            if actionable:
                await reply(templates.ask_customer(event.thread.subject))
                return "PROCESSED", "ASKED_WHICH_CUSTOMER"
            await reply(templates.help_text(event.thread.subject, self._settings))
            return "PROCESSED", "HELP_SENT"
        if not actionable:
            if case is not None:
                await reply(templates.status(case.ref, case.data, case.state))
                return "PROCESSED", f"STATUS_SENT_{case.state}"
            await reply(templates.help_text(event.thread.subject, self._settings))
            return "PROCESSED", "HELP_SENT"

        ctx = await self._session.context()
        try:
            person, _ = await self._bd.owned_person(person_id, bd_id, ctx)
        except CustomerNotFoundError:
            await reply(templates.not_your_customer(event.thread.subject, person_id))
            return "PROCESSED", "NOT_THIS_BDS_CUSTOMER"
        who = (
            " ".join(p for p in (person.first_name, person.last_name) if p) or person_id
        )
        view = await self._enrolment.customer_services(person_id, bd_id, ctx)
        notes: list[str] = []

        if requested:
            taken = {x.code for x in view.services if x.opted or x.requested}
            data = CaseData(
                existing_customer=True,
                name=who,
                mobile=person_id,
                first_payment=view.first_payment,
            )
            data.add_services(requested, self._settings)
            new = [c for c in data.services if c not in taken]
            already = [c for c in data.services if c in taken]
            notes += templates.unavailable_lines(data, self._settings)
            if already:
                names = {x.code: x.name for x in view.services}
                notes.append(
                    "Already opted: "
                    + ", ".join(names.get(c, c) for c in already)
                    + "."
                )
            if new:
                data.services = new
                if mention is not None:
                    data.bank_name = mention[1].bank_name or None
                    data.project_name = mention[1].project_name or None
                # Documents in this mail are for the new services: kept for
                # after their payment.
                data.add_documents(intake_event_id, event.attachments)
                data.add_refused(event.rejected_attachments)
                new_case = await self._db(
                    case_store.open_case,
                    origin_event_id=intake_event_id,
                    customer_index=0,
                    bd_id=bd_id,
                    bd_email=bd_email,
                    data=data,
                    key=self._key,
                )
                if new_case.state == "NEW":
                    new_case.person_id = person_id
                    await self._advance(new_case, reply_to=event.event_id)
                return "PROCESSED", f"SERVICES_REQUESTED CASE={new_case.ref}"

        uploaded: list[str] = []
        not_uploaded: list[str] = []
        if event.attachments:
            staged = CaseData()
            staged.add_documents(intake_event_id, event.attachments)
            uploadable = [x.code for x in view.services if x.can_upload]
            uploaded, not_uploaded = await self._upload(
                person_id, bd_id, staged.documents, uploadable, ctx
            )
            if uploaded:
                view = await self._enrolment.customer_services(person_id, bd_id, ctx)
        refused = [
            f"{r.filename} - {r.reason}".strip(" -") for r in event.rejected_attachments
        ]
        await reply(
            templates.customer_update(
                event.thread.subject,
                who,
                person_id,
                services=view.services,
                uploaded=uploaded,
                not_uploaded=not_uploaded,
                refused=refused,
                notes=notes,
            )
        )
        logger.info(
            "intake_customer_update person_id=%s uploaded=%d not_uploaded=%d",
            person_id,
            len(uploaded),
            len(not_uploaded),
        )
        return "PROCESSED", "CUSTOMER_UPDATE"

    async def _reply(
        self, case: Case, intake_event_id: str, event: IntakeEvent
    ) -> tuple[str, str]:
        # NEEDS_INFO: the BD answers our question. FAILED: the vendor refused
        # the details (e.g. a taken mobile) and the BD replies with a fix.
        # Anything else (payment choices, follow-ups) is a later phase.
        if case.state == "AWAITING_PAYMENT":
            return await self._payment_reply(case, intake_event_id, event)
        if (
            case.state == "DONE"
            and case.person_id
            and customer_paid(event.thread.subject, event.body_text, case.ref)
        ):
            return await self._customer_paid(case, event)
        if case.state == "DONE" and case.person_id:
            # A follow-up about a customer we finished with: documents,
            # more services or a status question.
            return await self._existing_customer(
                case.bd_id, case.bd_email, intake_event_id, event, case=case
            )
        if case.state not in REOPENABLE_STATES:
            # Never silence: say where the request stands.
            await self._mail(
                case,
                templates.status(case.ref, case.data, case.state),
                reply_to=event.event_id,
            )
            return "PROCESSED", f"STATUS_SENT_{case.state}"
        before = case.data.model_dump()
        mention = next(
            ((a, c) for a in event.actions for c in a.customers),
            None,
        )
        if mention is not None:
            case.data.merge_customer(mention[1], mention[0].services, self._settings)
        else:
            for a in event.actions:
                case.data.add_services(a.services, self._settings)
        for a in event.actions:
            if a.correction is not None:
                case.data.apply_correction(a.correction.field, a.correction.new)
        case.data.add_documents(intake_event_id, event.attachments)
        if case.state == "FAILED":
            if case.data.model_dump() == before:
                # Same details again would only be refused again.
                return "IGNORED", "REPLY_WITHOUT_CHANGES"
            logger.info("intake_case_reopened case=%s", case.ref)
            case.last_error = None
        await self._advance(case, reply_to=event.event_id)
        return "PROCESSED", f"CASE={case.ref}"

    # -- one case ----------------------------------------------------------

    async def _advance(self, case: Case, *, reply_to: str | None) -> None:
        missing = case.data.missing(self._settings)
        if missing:
            first_ask = case.state != "NEEDS_INFO"
            case.state = "NEEDS_INFO"
            if first_ask or case.expires_at is None:
                case.expires_at = datetime.now(timezone.utc) + timedelta(
                    hours=self._settings.intake_info_expiry_hours
                )
            case.reminders_sent = 0
            await self._db(case_store.save_case, case, self._key)
            await self._mail(
                case,
                templates.needs_info(
                    case.ref,
                    case.data,
                    missing,
                    self._settings,
                    reminder=False,
                    expires_on=_day(case.expires_at),
                ),
                reply_to=reply_to,
            )
            return
        case.expires_at = None
        if case.data.existing_customer and case.person_id:
            # Already registered (a services request): straight to payment.
            first = case.data.first_payment
            await self._awaiting_payment(
                case,
                joining_fee=None
                if first is None
                else (self._settings.joining_fee if first else 0),
                credentials_note="",
                problems=[],
                reply_to=reply_to,
            )
            return
        case.state = "REGISTERING"
        await self._db(case_store.save_case, case, self._key)
        await self._register(case, reply_to=reply_to)

    async def _register(self, case: Case, *, reply_to: str | None) -> None:
        req = case.data.to_create_request()
        problems: list[str] = []
        credentials_note = ""
        login: tuple[str, str] | None = None
        try:
            ctx = await self._session.context()
            try:
                result = await self._bd.create_customer(req, case.bd_id, ctx)
            except VendorError as exc:
                if exc.http_status != 401:
                    raise
                # Refused before anything happened: sign in again, once.
                self._session.invalidate()
                ctx = await self._session.context()
                result = await self._bd.create_customer(req, case.bd_id, ctx)
        except IntakeNotConfiguredError as exc:
            await self._stop(case, "NEEDS_ATTENTION", str(exc), notify=None)
            return
        except VendorError as exc:
            await self._registration_refused(case, exc, reply_to=reply_to)
            return

        case.person_id = result.person_id
        if result.login_created and result.temporary_password:
            # The BD gets them too (user, 2026-11-16) -- as the dashboard shows
            # them once. Only in the email: never stored with the case.
            login = (
                result.login_user_id or result.person_id,
                result.temporary_password,
            )
        if not result.wallet_opened:
            problems.append(
                "The customer's wallet could not be opened yet; retry it from the "
                "dashboard (Open wallet)."
            )
        if not result.login_created:
            problems.append(
                "The customer's login could not be created yet; retry it from the "
                "dashboard (Create login)."
            )
            credentials_note = "No login details were sent to the customer yet."
        elif result.credentials_emailed:
            credentials_note = (
                "Their login details were emailed to the customer at "
                f"{req.email_address}."
            )
        else:
            credentials_note = (
                "Their login details could not be emailed to the customer; please "
                "reset the password from the dashboard."
            )
        logger.info(
            "intake_registered case=%s person_id=%s wallet=%s login=%s",
            case.ref,
            result.person_id,
            result.wallet_opened,
            result.login_created,
        )
        await self._awaiting_payment(
            case,
            joining_fee=self._settings.joining_fee,
            credentials_note=credentials_note,
            problems=problems,
            reply_to=reply_to,
            login=login,
        )

    async def _registration_refused(
        self, case: Case, exc: VendorError, *, reply_to: str | None
    ) -> None:
        details = error_details(exc)
        if exc.http_status == 409 or exc.error_code in ALREADY_REGISTERED_CODES:
            # Already registered. Fine if it is this BD's customer; never
            # register again (no vendor upsert exists). The sandbox reports a
            # taken mobile as 400 MOBILE_NUMBER_ALREADY_EXISTS, not 409
            # (seen live 2026-11-16).
            try:
                ctx = await self._session.context()
                await self._bd.owned_person(case.data.mobile, case.bd_id, ctx)
            except CustomerNotFoundError:
                # Not this BD's customer with this mobile: something else is
                # taken -- the mobile on another account, or (a bare 409, seen
                # live 2026-11-16) the email address. The vendor's own reason
                # names which, when it sends a code.
                code = details.get("code") or "CONFLICT"
                await self._stop(
                    case,
                    "FAILED",
                    f"ALREADY_REGISTERED_ELSEWHERE {exc.http_status} {code}",
                    notify=(
                        f"The registration was refused: {details['message']} "
                        "The mobile number or email address is already used by "
                        "another account (not one of your customers). Reply to "
                        "this email with a different mobile number or email "
                        "address and we will try again."
                    ),
                    reply_to=reply_to,
                )
                return
            except VendorError as lookup:
                await self._stop(
                    case,
                    "NEEDS_ATTENTION",
                    f"409 then lookup {lookup.http_status}",
                    notify=None,
                )
                return
            case.person_id = case.data.mobile
            case.data.existing_customer = True
            await self._awaiting_payment(
                case,
                joining_fee=None,
                credentials_note=(
                    "Their existing login is unchanged; no new login details were sent."
                ),
                problems=[],
                reply_to=reply_to,
            )
            return
        if 400 <= exc.http_status < 500:
            await self._stop(
                case,
                "FAILED",
                f"{exc.http_status} {details.get('code') or ''}".strip(),
                notify=(
                    f"The registration was refused: {details['message']} "
                    "Reply to this email with the corrected details and we will "
                    "try again, or add the customer from the dashboard."
                ),
                reply_to=reply_to,
            )
            return
        # 5xx / timeout: the person may or may not exist now. Never retried.
        await self._stop(
            case,
            "NEEDS_ATTENTION",
            f"{exc.http_status} {details.get('code') or ''}".strip(),
            notify=(
                "We could not confirm whether the registration went through. "
                "Please check the dashboard before sending this customer again; "
                "our team has been alerted."
            ),
            reply_to=reply_to,
        )

    async def _awaiting_payment(
        self,
        case: Case,
        *,
        joining_fee: int | None,
        credentials_note: str,
        problems: list[str],
        reply_to: str | None,
        login: tuple[str, str] | None = None,
    ) -> None:
        case.state = "AWAITING_PAYMENT"
        case.last_error = None
        await self._db(case_store.save_case, case, self._key)
        await self._mail(
            case,
            templates.registered(
                case.ref,
                case.data,
                self._settings,
                services=case.data.chosen_services(self._settings),
                joining_fee=joining_fee,
                credentials_note=credentials_note,
                problems=problems,
                login=login,
            ),
            reply_to=reply_to,
        )

    # -- Phase 2: payment and documents ----------------------------------------

    async def _payment_reply(
        self, case: Case, intake_event_id: str, event: IntakeEvent
    ) -> tuple[str, str]:
        before = len(case.data.documents) + len(case.data.refused_files)
        case.data.add_documents(intake_event_id, event.attachments)
        case.data.add_refused(event.rejected_attachments)
        if len(case.data.documents) + len(case.data.refused_files) != before:
            # Uploaded once the payment is recorded.
            await self._db(case_store.save_case, case, self._key)
        choice = payment_choice(event.thread.subject, event.body_text, case.ref)
        if choice is None:
            await self._mail(
                case,
                templates.ask_choice(case.ref, case.data, self._settings),
                reply_to=event.event_id,
            )
            return "PROCESSED", "ASKED_PAYMENT_CHOICE"
        mode = "pay_now" if choice == templates.PAY_NOW else "pay_later"
        # Single use: from here on a repeated reply only gets a status mail.
        case.state = "PAYING"
        await self._db(case_store.save_case, case, self._key)
        await self._pay(case, mode, reply_to=event.event_id)
        return "PROCESSED", f"PAYMENT_{mode.upper()}"

    async def _pay(self, case: Case, mode: str, *, reply_to: str) -> None:
        later = mode == "pay_later"
        req = PaymentRequest(
            services=case.data.services,
            mode=mode,
            # Pay later: the BD's reply says the customer agreed and the BD
            # stands guarantor (the button's wording) -- both consents.
            bd_consent=later,
            customer_consent=later,
            details=case.data.service_details(self._settings),
        )
        try:
            ctx = await self._session.context()
            result = await self._enrolment.pay(case.person_id, req, case.bd_id, ctx)
        except IntakeNotConfiguredError as exc:
            await self._stop(case, "NEEDS_ATTENTION", str(exc), notify=None)
            return
        except PaymentOutcomeUnknownError as exc:
            await self._stop(
                case,
                "NEEDS_ATTENTION",
                f"PAYMENT_OUTCOME_UNKNOWN {exc.cause.http_status}",
                notify=(
                    "We could not confirm whether the pay-later transfer went "
                    "through. Our team will check it with the bank. Please do not "
                    "pay again for this customer meanwhile."
                ),
                reply_to=reply_to,
            )
            return
        except (
            PaymentRequestError,
            PaymentNotAllowedError,
            UnknownServiceError,
        ) as exc:
            await self._payment_not_done(case, str(exc), reply_to=reply_to)
            return
        except CustomerNotFoundError:
            await self._stop(
                case, "NEEDS_ATTENTION", "PERSON_NOT_FOUND_AT_PAYMENT", notify=None
            )
            return
        except VendorError as exc:
            refused = (
                400 <= exc.http_status < 500
                or exc.error_code == "TRANSFER_NOT_PERMITTED"
            )
            if exc.http_status == 401:
                self._session.invalidate()
            if refused:
                # Nothing charged: the claim was released (or never made).
                await self._payment_not_done(
                    case, error_details(exc)["message"], reply_to=reply_to
                )
                return
            await self._stop(
                case,
                "NEEDS_ATTENTION",
                f"PAYMENT_VENDOR_{exc.http_status} {exc.error_code or ''}".strip(),
                notify=(
                    "We could not confirm whether the payment was recorded. Our "
                    "team will check. Please do not pay again for this customer "
                    "meanwhile."
                ),
                reply_to=reply_to,
            )
            return

        txn = result.transfer.transaction_id if result.transfer else None
        case.data.paid_mode = mode
        case.data.paid_amount = result.amount
        case.data.transaction_id = txn
        if not result.status_recorded:
            # Money moved, but marking the customer paid failed.
            await self._stop(
                case,
                "NEEDS_ATTENTION",
                f"PAID_BUT_NOT_RECORDED txn={txn}",
                notify=(
                    f"The transfer went through (transaction {txn}), but we could "
                    "not mark the customer as paid. Our team will fix it. Please do "
                    "not pay again."
                ),
                reply_to=reply_to,
            )
            return
        logger.info(
            "intake_paid case=%s person_id=%s mode=%s amount=%s txn=%s",
            case.ref,
            case.person_id,
            mode,
            result.amount,
            txn,
        )
        case.state = "UPLOADING"
        await self._db(case_store.save_case, case, self._key)
        uploaded, not_uploaded = await self._upload_documents(case, ctx)
        case.state = "DONE"
        await self._db(case_store.save_case, case, self._key)
        names = {s.code: s.name for s in self._settings.services}
        await self._mail(
            case,
            templates.paid(
                case.ref,
                case.data,
                mode=mode,
                amount=result.amount,
                services=[names.get(c, c) for c in result.services],
                transaction_id=txn,
                uploaded=uploaded,
                not_uploaded=not_uploaded,
            ),
            reply_to=reply_to,
        )

    async def _payment_not_done(
        self, case: Case, reason: str, *, reply_to: str
    ) -> None:
        case.state = "AWAITING_PAYMENT"
        case.last_error = reason[:500]
        await self._db(case_store.save_case, case, self._key)
        logger.warning("intake_payment_not_done case=%s reason=%s", case.ref, reason)
        await self._mail(
            case,
            templates.payment_refused(case.ref, case.data, self._settings, reason),
            reply_to=reply_to,
        )

    async def _upload_documents(self, case: Case, ctx) -> tuple[list[str], list[str]]:
        return await self._upload(
            case.person_id,
            case.bd_id,
            case.data.documents,
            list(case.data.services),
            ctx,
        )

    async def _upload(
        self,
        person_id: str,
        bd_id: str,
        documents: list[CaseDocument],
        chosen: list[str],
        ctx,
    ) -> tuple[list[str], list[str]]:
        """One document per service in `chosen`, as on the dashboard: by the
        document's class, or every file to the only service there is."""
        mapping = self._settings.intake_doc_class_service_map
        names = {s.code: s.name for s in self._settings.services}
        picked: dict[str, CaseDocument] = {}
        not_uploaded: list[str] = []
        for d in documents:
            if not d.has_content:
                why = d.omitted_reason or "not accepted"
                not_uploaded.append(f"{d.filename} - not received ({why})")
                continue
            problem = document_problem(d.content_type, d.size_bytes, self._settings)
            if problem:
                not_uploaded.append(f"{d.filename} - {problem}")
                continue
            target = mapping.get(d.doc_class or "")
            if target not in chosen:
                target = chosen[0] if len(chosen) == 1 else None
            if target is None:
                not_uploaded.append(
                    f"{d.filename} - "
                    + (
                        "no paid service can take a document yet"
                        if not chosen
                        else "could not tell which service it is for"
                    )
                )
                continue
            if target in picked:
                not_uploaded.append(
                    f"{d.filename} - {names.get(target, target)} takes one document "
                    f"({picked[target].filename} was uploaded)"
                )
                continue
            picked[target] = d
        uploaded: list[str] = []
        for code, d in picked.items():
            data = await self._db(
                store.load_attachment, d.intake_event_id, d.attachment_id, self._key
            )
            if data is None:
                not_uploaded.append(f"{d.filename} - not received")
                continue
            try:
                await self._enrolment.upload_document(
                    person_id,
                    code,
                    StoredFile(name=d.filename, content_type=d.content_type, data=data),
                    bd_id,
                    ctx,
                )
            except (DocumentNotAllowedError, UnknownServiceError) as exc:
                not_uploaded.append(f"{d.filename} - {exc}")
                continue
            except VendorError as exc:
                not_uploaded.append(f"{d.filename} - {error_details(exc)['message']}")
                continue
            note = await self._queue_analysis(person_id, bd_id, code, d, data, ctx)
            uploaded.append(f"{d.filename} ({names.get(code, code)}){note}")
        return uploaded, not_uploaded

    async def _queue_analysis(
        self, person_id: str, bd_id: str, code: str, d: CaseDocument, data: bytes, ctx
    ) -> str:
        """Queue the DPR analysis of an emailed document, exactly as a
        dashboard upload with analysis does. Returns a note for the BD."""
        s = self._settings
        if code not in s.intake_analyse_services:
            return ""
        if not (s.dpr_agentic_enabled and s.dpr_agentic_gemini_api_key):
            return " - DPR analysis is not available right now"
        try:
            svc = self._enrolment._service(code)  # noqa: SLF001
            values = await self._enrolment._values(person_id, bd_id, ctx)  # noqa: SLF001
            status, run_id = await asyncio.to_thread(
                queue_analysis,
                self._dpr_engine,
                s,
                person_id=person_id,
                code=code,
                file=StoredFile(
                    name=d.filename, content_type=d.content_type, data=data
                ),
                project_context=_project_context(svc, values),
                wallet_status="STORED",
            )
        except Exception:  # never undo a stored document over its analysis
            logger.exception("intake_analysis_not_queued person_id=%s", person_id)
            return " - DPR analysis could not be started; start it from the dashboard"
        logger.info(
            "intake_analysis status=%s run_id=%s person_id=%s",
            status,
            run_id,
            person_id,
        )
        return {
            "QUEUED": " - DPR analysis started; the report appears on the dashboard",
            "RATE_LIMITED": " - DPR analysis not started: today's limit for this "
            "customer is reached; start it from the dashboard tomorrow",
            "ALREADY_ANALYSED": " - this DPR was already analysed; its report is "
            "on the dashboard",
        }.get(status, " - DPR analysis is not available right now")

    async def _stop(
        self,
        case: Case,
        state: str,
        error: str,
        *,
        notify: str | None,
        reply_to: str | None = None,
    ) -> None:
        case.state = state
        case.last_error = error
        await self._db(case_store.save_case, case, self._key)
        log = logger.error if state == "NEEDS_ATTENTION" else logger.warning
        log("intake_case_stopped case=%s state=%s error=%s", case.ref, state, error)
        if notify:
            await self._mail(
                case, templates.failed(case.ref, case.data, notify), reply_to=reply_to
            )

    async def _follow_up(self, case_id: str, what: str) -> None:
        case = await self._db(case_store.load_case, case_id, self._key)
        if case is None or case.state != "NEEDS_INFO":
            return
        if what == "EXPIRE":
            case.state = "EXPIRED"
            await self._db(case_store.save_case, case, self._key)
            await self._mail(case, templates.expired(case.ref, case.data))
            return
        case.reminders_sent += 1
        await self._db(case_store.save_case, case, self._key)
        await self._mail(
            case,
            templates.needs_info(
                case.ref,
                case.data,
                case.data.missing(self._settings),
                self._settings,
                reminder=True,
                expires_on=_day(case.expires_at),
            ),
        )

    async def _mail(
        self, case: Case, mail: Mail, *, reply_to: str | None = None
    ) -> None:
        """Send to the case's BD, threaded on the last mail of the case (or
        the BD's own email). A failed send is logged, never fatal."""
        previous = await self._db(case_store.last_message_id, case.case_id)
        parent = reply_to or previous
        mail = replace(
            mail,
            to=case.bd_email,
            in_reply_to=parent,
            references=tuple(m for m in (previous,) if m and m != parent),
        )
        try:
            message_id = await self._mailer.send(mail)
        except (MailNotConfiguredError, MailSendError) as exc:
            logger.warning(
                "intake_mail_not_sent case=%s error=%s", case.ref, type(exc).__name__
            )
            return
        await self._db(case_store.record_message, case.case_id, message_id)


def _day(when: datetime | None) -> str:
    return when.astimezone(timezone.utc).strftime("%d %b %Y") if when else "soon"
