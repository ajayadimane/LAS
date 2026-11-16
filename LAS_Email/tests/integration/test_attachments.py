"""Attachments: walk (zip, nested mail), allow-list, AV, size, password check, encrypted store,
classification, metering -- and refused files reaching Core LAS (which tells the BD)."""

from email.message import EmailMessage

from las.common.db import SessionFactory
from las.common.deps import Deps
from las.common.fakes import EICAR
from las.common.models import EmailAttachment, InboundEmail, MailAction
from las.core_handoff import build_event
from tests.docs import locked_pdf, pdf, png, zip_of
from tests.helpers import (
    action,
    customer,
    deliver,
    extraction,
    llm_by_prompt,
    make_mail,
    rows,
    script_llm,
)

PDF = "application/pdf"
STATUS = extraction(action("STATUS_QUERY"))


async def _deliver_with(
    deps: Deps,
    attachments: list[tuple[str, str, bytes]],
    llm: str = STATUS,
    body: str = "please see attached",
) -> InboundEmail:
    script_llm(deps, llm)
    return await deliver(deps, make_mail(body=body, attachments=attachments))


async def test_refused_files_are_recorded_and_forwarded_to_core(
    deps: Deps, db: SessionFactory
) -> None:
    deps.settings.attachment_max_bytes = 200_000
    inbound = await _deliver_with(
        deps,
        [
            ("virus.pdf", PDF, pdf() + EICAR),
            ("setup.exe", "application/octet-stream", b"MZ\x90\x00" + b"\x00" * 200),
            ("big.pdf", PDF, pdf() + b"0" * 300_000),
            ("locked.pdf", PDF, locked_pdf()),
            ("bomb.zip", "application/zip", zip_of({"zeros.pdf": b"\x00" * 5_000_000})),
        ],
    )
    atts = {a.original_filename: a for a in await rows(db, EmailAttachment)}
    assert atts["virus.pdf"].av_status == "INFECTED" and atts["virus.pdf"].storage_uri.startswith(
        "mem://quarantine/"
    )
    assert atts["locked.pdf"].is_password_protected
    assert "setup.exe" not in atts and "big.pdf" not in atts

    event = await build_event(deps, inbound.inbound_id)
    refused = {r["filename"]: r["reason"] for r in event["rejected_attachments"]}
    assert "file type not accepted" in refused["setup.exe"]
    assert "big.pdf" in refused and "zeros.pdf" in refused
    sent = {a["filename"]: a for a in event["attachments"]}
    # Stored but unusable files go as metadata only, with the reason.
    assert sent["virus.pdf"]["content_base64"] is None
    assert sent["virus.pdf"]["content_omitted_reason"] == "NOT_SCANNED_CLEAN"
    assert sent["locked.pdf"]["content_omitted_reason"] == "PASSWORD_PROTECTED"


async def test_zip_and_nested_mail_are_walked(deps: Deps, db: SessionFactory) -> None:
    inner = EmailMessage()
    inner["Subject"] = "fwd: land papers"
    inner.set_content("Onboard Suresh 9123456780")
    inner.add_attachment(
        pdf("RTC Pahani Survey No 12"), maintype="application", subtype="pdf", filename="rtc.pdf"
    )
    await _deliver_with(
        deps,
        [
            (
                "docs.zip",
                "application/zip",
                zip_of({"a/dpr.pdf": pdf(), "quote.pdf": pdf("Quotation No 7")}),
            ),
            ("fwd.eml", "message/rfc822", inner.as_bytes()),
        ],
    )
    atts = {a.original_filename: a for a in await rows(db, EmailAttachment)}
    assert set(atts) == {"dpr.pdf", "quote.pdf", "rtc.pdf"}
    assert atts["dpr.pdf"].doc_class == "DPR" and atts["quote.pdf"].doc_class == "QUOTATION"
    assert atts["rtc.pdf"].doc_class == "LAND_RECORD"
    assert all(a.av_status == "CLEAN" and len(a.sha256) == 64 for a in atts.values())
    ib = (await rows(db, InboundEmail))[0]
    assert "Onboard Suresh" in ib.parsed_payload["nested_texts"][0]


async def test_llm_classification_fallback_and_ocr_are_metered(
    deps: Deps, db: SessionFactory
) -> None:
    from las.common.models import AiUsage

    llm_by_prompt(
        deps,
        {
            "intent_extract": STATUS,
            "doc_classify": '{"doc_class": "PAYMENT_PROOF", "confidence": 0.9}',
        },
    )
    await deliver(deps, make_mail(body="see", attachments=[("IMG_2201.png", "image/png", png())]))
    (att,) = await rows(db, EmailAttachment)
    assert att.doc_class == "PAYMENT_PROOF"
    purposes = sorted(u.purpose for u in await rows(db, AiUsage))
    assert purposes == ["DOC_CLASSIFICATION", "INTENT_EXTRACTION", "OCR"]
    ocr = next(u for u in await rows(db, AiUsage) if u.purpose == "OCR")
    assert ocr.units == 1 and ocr.attachment_id == att.attachment_id


async def test_filename_alone_does_not_ground_a_customer_name(
    deps: Deps, db: SessionFactory
) -> None:
    """Reproduces a production incident: a mail with no customer details in the body, just an
    attachment named after a customer ('somaning.pdf'). The model guessed the filename as the
    customer's name, but a filename is not evidence of whose document it is (a BD can name a file
    anything): the name must not be passed on as grounded."""
    await _deliver_with(
        deps,
        [("somaning.pdf", PDF, pdf("Detailed Project Report for Vaseem Khan"))],
        llm=extraction(
            action(
                "SUBMIT_DOCUMENT",
                customers=[customer("somaning")],
                attachment_filenames=["somaning.pdf"],
            )
        ),
        body="pfa",
    )
    (act,) = await rows(db, MailAction)
    assert act.entities["customers"][0]["name"] is None
    assert any(
        p["field"] == "customers.0.name" and p["kind"] == "ungrounded"
        for p in act.entities["_problems"]
    )
