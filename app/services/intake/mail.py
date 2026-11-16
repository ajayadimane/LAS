"""Emails to the BD about an emailed customer (plain text + HTML).

Every subject carries [LAS-IN-<ref>] so a reply finds its case even when the
BD writes a new mail instead of replying. Values from the BD's own email are
HTML-escaped: they came from untrusted mail.
"""

from html import escape
from urllib.parse import quote

from app.core.config import ServiceConfig, Settings
from app.services.intake.case_data import CaseData
from app.services.intake.cases import subject_tag
from app.services.mailer import Mail

PAY_NOW = "PAY_NOW"
PAY_LATER = "PAY_LATER"

_OMITTED = {
    "TOO_LARGE": "too large",
    "TOTAL_TOO_LARGE": "too large together with the other files",
    "TYPE_NOT_ACCEPTED": "file type not accepted (PDF, JPG or PNG only)",
    "PASSWORD_PROTECTED": "password protected",
    "NOT_SCANNED_CLEAN": "failed the virus scan",
    "CHECKSUM_MISMATCH": "could not be read",
}

_FOOTER_PLAIN = "This is an automated message from LAS."
_FOOTER = (
    "This is an automated message from LAS. Reply to this email to answer; "
    "keep the [LAS-IN-...] code in the subject."
)


def _who(data: CaseData) -> str:
    return data.name or "your customer"


def _rupees(amount: int) -> str:
    return f"Rs {amount:,}"


def _so_far(data: CaseData, settings: Settings) -> list[tuple[str, str]]:
    address = ", ".join(
        p
        for p in (data.street_address, data.city, data.state_province, data.postal_code)
        if p
    )
    services = ", ".join(s.name for s in data.chosen_services(settings))
    rows = [
        ("Name", data.name or "-"),
        ("Mobile", data.mobile or "-"),
        ("Email", data.email or "-"),
        ("Address", address or "-"),
        ("Services", services or "-"),
    ]
    if data.bank_name:
        rows.append(("Bank", data.bank_name))
    if data.project_name:
        rows.append(("Project", data.project_name))
    return rows


def _html(paragraphs: list[str]) -> str:
    body = "".join(paragraphs)
    return (
        '<div style="font-family:Arial,sans-serif;font-size:14px;color:#1f2937;'
        f'line-height:1.5">{body}<p style="color:#6b7280;font-size:12px">'
        f"{escape(_FOOTER)}</p></div>"
    )


def _table(rows: list[tuple[str, str]]) -> str:
    cells = "".join(
        f'<tr><td style="padding:2px 12px 2px 0;color:#6b7280">{escape(k)}</td>'
        f"<td>{escape(v)}</td></tr>"
        for k, v in rows
    )
    return f"<table>{cells}</table>"


def _list(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{escape(i)}</li>" for i in items) + "</ul>"


def _unavailable_lines(data: CaseData, settings: Settings) -> list[str]:
    portal = {s.code: s for s in settings.services if s.external}
    lines = []
    for code in data.unavailable_services:
        if code in portal:
            s = portal[code]
            lines.append(
                f"{s.name} is handled on the partner portal"
                + (f" ({s.portal_url})" if s.portal_url else "")
                + ", not by email."
            )
        else:
            lines.append(f"We do not offer '{code}' by email.")
    return lines


def needs_info(
    ref: str,
    data: CaseData,
    missing: list[str],
    settings: Settings,
    *,
    reminder: bool,
    expires_on: str,
) -> Mail:
    who = _who(data)
    lead = (
        f"A reminder: we still cannot register {who}."
        if reminder
        else f"Thank you. To register {who} we still need a few details."
    )
    notes = _unavailable_lines(data, settings)
    text = "\n".join(
        [
            "Hello,",
            "",
            lead,
            "",
            "Please reply to this email with:",
            *[f"  - {m}" for m in missing],
            "",
            "What we have so far:",
            *[f"  {k}: {v}" for k, v in _so_far(data, settings)],
            *(["", *notes] if notes else []),
            "",
            f"This request will be closed on {expires_on} if we do not hear back.",
            "",
            _FOOTER,
        ]
    )
    html = _html(
        [
            f"<p>Hello,</p><p>{escape(lead)}</p>",
            "<p><b>Please reply to this email with:</b></p>",
            _list(missing),
            "<p><b>What we have so far:</b></p>",
            _table(_so_far(data, settings)),
            *[f"<p>{escape(n)}</p>" for n in notes],
            f"<p>This request will be closed on {escape(expires_on)} if we do not "
            "hear back.</p>",
        ]
    )
    prefix = "Reminder: details" if reminder else "Details"
    return Mail(
        to="",
        subject=f"{subject_tag(ref)} {prefix} needed to register {who}",
        text=text,
        html=html,
        auto_submitted=True,
    )


def _pay_link(mailbox: str, ref: str, choice: str) -> str:
    subject = quote(f"LAS-PAY {ref} {choice} {subject_tag(ref)}")
    body = quote(
        "Customer has paid (Pay now)."
        if choice == PAY_NOW
        else "Customer will pay later. I confirm the customer agreed and I stand "
        "guarantor (Pay later)."
    )
    return f"mailto:{mailbox}?subject={subject}&body={body}"


def _button(href: str, label: str, color: str) -> str:
    return (
        f'<a href="{escape(href)}" style="display:inline-block;padding:10px 18px;'
        f"margin:4px 8px 4px 0;background:{color};color:#ffffff;"
        f'text-decoration:none;border-radius:6px;font-weight:bold">{escape(label)}</a>'
    )


def registered(
    ref: str,
    data: CaseData,
    settings: Settings,
    *,
    services: list[ServiceConfig],
    joining_fee: int | None,
    credentials_note: str,
    problems: list[str],
    login: tuple[str, str] | None = None,
) -> Mail:
    """Registered: choose Pay now or Pay later. `joining_fee` None = unknown
    (an existing customer: charged only if this is their first payment)."""
    who = _who(data)
    lines = [(s.name, _rupees(s.fee)) for s in services]
    total = sum(s.fee for s in services)
    if joining_fee:
        lines.append(("Joining fee (first payment only)", _rupees(joining_fee)))
        total += joining_fee
    fee_note = (
        f"Plus the {_rupees(settings.joining_fee)} joining fee if this is the "
        "customer's first payment."
        if joining_fee is None
        else None
    )
    mailbox = settings.mail_from
    pay_now = _pay_link(mailbox, ref, PAY_NOW)
    pay_later = _pay_link(mailbox, ref, PAY_LATER)
    upi = settings.intake_pay_now_upi_id
    now_text = (
        f"Pay now: the customer pays {_rupees(total)} to UPI {upi}; reply PAY NOW "
        "once you have received it."
        if upi
        else f"Pay now: once the customer has paid {_rupees(total)}, reply PAY NOW."
    )
    later_text = (
        "Pay later: reply PAY LATER. You confirm the customer agreed to pay later "
        "and you stand guarantor, as on the dashboard."
    )
    docs = [
        d.filename
        + (
            f" - not stored: {_OMITTED.get(d.omitted_reason or '', 'not accepted')}"
            if not d.has_content
            else ""
        )
        for d in data.documents
    ]
    notes = [*problems, *_unavailable_lines(data, settings)]
    existing = (
        f"{who} was already registered under you, so we used that registration."
        if data.existing_customer
        else f"{who} is registered (customer id {data.mobile})."
    )

    login_lines = (
        [
            "",
            "Customer's login details (share them only with the customer):",
            f"  User id: {login[0]}",
            f"  Temporary password: {login[1]}",
        ]
        if login
        else []
    )
    text = "\n".join(
        [
            "Hello,",
            "",
            existing,
            credentials_note,
            *login_lines,
            "",
            "Services:",
            *[f"  {k}: {v}" for k, v in lines],
            f"  Total: {_rupees(total)}",
            *([f"  {fee_note}"] if fee_note else []),
            "",
            "How will the customer pay? Reply to this email with one of:",
            f"  {now_text}",
            f"  {later_text}",
            *(
                [
                    "",
                    "Documents received (uploaded once payment is confirmed):",
                    *[f"  - {d}" for d in docs],
                ]
                if docs
                else []
            ),
            *(["", *notes] if notes else []),
            "",
            _FOOTER,
        ]
    )
    html = _html(
        [
            f"<p>Hello,</p><p>{escape(existing)}<br>{escape(credentials_note)}</p>",
            (
                "<p><b>Customer's login details</b> (share them only with the "
                "customer):</p>"
                + _table([("User id", login[0]), ("Temporary password", login[1])])
                if login
                else ""
            ),
            "<p><b>Services</b></p>",
            _table([*lines, ("Total", _rupees(total))]),
            f"<p>{escape(fee_note)}</p>" if fee_note else "",
            "<p><b>How will the customer pay?</b></p>",
            f"<p>{_button(pay_now, 'Pay now', '#1F5E46')}"
            f"{_button(pay_later, 'Pay later', '#9A5408')}</p>",
            f"<p>{escape(now_text)}<br>{escape(later_text)}</p>",
            (
                "<p><b>Documents received</b> (uploaded once payment is confirmed):</p>"
                + _list(docs)
                if docs
                else ""
            ),
            *[f"<p>{escape(n)}</p>" for n in notes],
        ]
    )
    return Mail(
        to="",
        subject=f"{subject_tag(ref)} {who} registered - choose how they pay",
        text=text,
        html=html,
        auto_submitted=True,
    )


def failed(ref: str, data: CaseData, reason: str) -> Mail:
    who = _who(data)
    text = "\n".join(
        ["Hello,", "", f"We could not register {who}.", "", reason, "", _FOOTER]
    )
    html = _html(
        [
            f"<p>Hello,</p><p>We could not register {escape(who)}.</p>"
            f"<p>{escape(reason)}</p>"
        ]
    )
    return Mail(
        to="",
        subject=f"{subject_tag(ref)} Could not register {who}",
        text=text,
        html=html,
        auto_submitted=True,
    )


def expired(ref: str, data: CaseData) -> Mail:
    who = _who(data)
    reason = (
        f"We did not receive the missing details for {who}, so this request is "
        "closed. Send a new email with all the details to start again."
    )
    text = "\n".join(["Hello,", "", reason, "", _FOOTER])
    html = _html([f"<p>Hello,</p><p>{escape(reason)}</p>"])
    return Mail(
        to="",
        subject=f"{subject_tag(ref)} Request closed: {who}",
        text=text,
        html=html,
        auto_submitted=True,
    )


def not_registered(their_subject: str, settings: Settings) -> Mail:
    """The reply to an authenticated sender who is not a registered BD."""
    contact = (
        f" ({settings.intake_admin_contact})" if settings.intake_admin_contact else ""
    )
    message = (
        "You are not a registered BD. Please contact the LAS Admin"
        f"{contact} to register as a BD."
    )
    subject = " ".join((their_subject or "").split())[:150]
    text = "\n".join(["Hello,", "", message, "", _FOOTER_PLAIN])
    html = (
        '<div style="font-family:Arial,sans-serif;font-size:14px;color:#1f2937;'
        f'line-height:1.5"><p>Hello,</p><p>{escape(message)}</p>'
        f'<p style="color:#6b7280;font-size:12px">{escape(_FOOTER_PLAIN)}</p></div>'
    )
    return Mail(
        to="",
        subject=f"Re: {subject}" if subject else "LAS: not a registered BD",
        text=text,
        html=html,
        auto_submitted=True,
    )


def _choice_buttons(ref: str, settings: Settings) -> tuple[str, str]:
    """The Pay now / Pay later buttons again, as (html, text)."""
    mailbox = settings.mail_from
    html = (
        f"<p>{_button(_pay_link(mailbox, ref, PAY_NOW), 'Pay now', '#1F5E46')}"
        f"{_button(_pay_link(mailbox, ref, PAY_LATER), 'Pay later', '#9A5408')}</p>"
    )
    text = (
        "Reply to this email with PAY NOW (the customer has paid) or PAY LATER "
        "(the customer will pay later and you stand guarantor)."
    )
    return html, text


def ask_choice(ref: str, data: CaseData, settings: Settings) -> Mail:
    """The reply did not clearly say Pay now or Pay later."""
    who = _who(data)
    buttons, how = _choice_buttons(ref, settings)
    lead = f"We could not tell how {who} will pay from your reply."
    text = "\n".join(["Hello,", "", lead, how, "", _FOOTER])
    html = _html(
        [f"<p>Hello,</p><p>{escape(lead)}</p>", buttons, f"<p>{escape(how)}</p>"]
    )
    return Mail(
        to="",
        subject=f"{subject_tag(ref)} How will {who} pay?",
        text=text,
        html=html,
        auto_submitted=True,
    )


def payment_refused(ref: str, data: CaseData, settings: Settings, reason: str) -> Mail:
    """Nothing was charged; the BD may choose again."""
    who = _who(data)
    buttons, how = _choice_buttons(ref, settings)
    lead = f"The payment for {who} did not go through, and nothing was charged."
    text = "\n".join(["Hello,", "", lead, reason, "", how, "", _FOOTER])
    html = _html(
        [
            f"<p>Hello,</p><p>{escape(lead)}</p><p>{escape(reason)}</p>",
            buttons,
            f"<p>{escape(how)}</p>",
        ]
    )
    return Mail(
        to="",
        subject=f"{subject_tag(ref)} Payment not completed for {who}",
        text=text,
        html=html,
        auto_submitted=True,
    )


def paid(
    ref: str,
    data: CaseData,
    *,
    mode: str,
    amount: int,
    services: list[str],
    transaction_id: str | None,
    uploaded: list[str],
    not_uploaded: list[str],
) -> Mail:
    """Services opted and paid; documents uploaded where possible."""
    who = _who(data)
    how = "Pay now (received by you)" if mode == "pay_now" else "Pay later"
    rows = [
        ("Customer", f"{who} ({data.mobile})"),
        ("Services", ", ".join(services)),
        ("Payment", how),
        ("Amount", _rupees(amount)),
    ]
    if transaction_id:
        rows.append(("Transaction id", transaction_id))
    lines = [
        "Hello,",
        "",
        f"Payment recorded for {who}. The services are now active.",
        "",
        *[f"  {k}: {v}" for k, v in rows],
    ]
    if uploaded:
        lines += ["", "Documents uploaded:", *[f"  - {d}" for d in uploaded]]
    if not_uploaded:
        lines += [
            "",
            "Not uploaded (please upload from the dashboard):",
            *[f"  - {d}" for d in not_uploaded],
        ]
    if not data.documents:
        lines += [
            "",
            "No documents were attached; please upload them from the dashboard.",
        ]
    lines += ["", _FOOTER]
    html = _html(
        [
            f"<p>Hello,</p><p>Payment recorded for {escape(who)}. The services are "
            "now active.</p>",
            _table(rows),
            "<p><b>Documents uploaded:</b></p>" + _list(uploaded) if uploaded else "",
            (
                "<p><b>Not uploaded</b> (please upload from the dashboard):</p>"
                + _list(not_uploaded)
                if not_uploaded
                else ""
            ),
            (
                "<p>No documents were attached; please upload them from the "
                "dashboard.</p>"
                if not data.documents
                else ""
            ),
        ]
    )
    return Mail(
        to="",
        subject=f"{subject_tag(ref)} Payment recorded for {who}",
        text="\n".join(lines),
        html=html,
        auto_submitted=True,
    )


_STATE_TEXT = {
    "REGISTERING": "We are registering this customer right now.",
    "PAYING": "We are processing the payment right now; you will hear from us shortly.",
    "DONE": "This request is complete: the customer is registered and paid. "
    "Use the dashboard for anything further.",
    "NEEDS_ATTENTION": "Our team is checking this request; you will hear from them. "
    "Please do not resend it meanwhile.",
    "EXPIRED": "This request was closed. Send a new email with all the details to "
    "start again.",
}


def status(ref: str, data: CaseData, state: str) -> Mail:
    """A reply we cannot act on in this state: say where the request stands."""
    who = _who(data)
    line = _STATE_TEXT.get(state, "This request cannot take that reply.")
    text = "\n".join(["Hello,", "", f"About {who}: {line}", "", _FOOTER])
    html = _html([f"<p>Hello,</p><p>About {escape(who)}: {escape(line)}</p>"])
    return Mail(
        to="",
        subject=f"{subject_tag(ref)} Status of {who}",
        text=text,
        html=html,
        auto_submitted=True,
    )
