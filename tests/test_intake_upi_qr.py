"""The Pay now UPI QR in intake emails (app/services/intake/upi.py): the same
payment link as the dashboards, drawn as an inline PNG. No database needed."""

import dataclasses
import struct

import pytest

from app.core.config import Settings
from app.services.intake import mail as templates
from app.services.intake.case_data import CaseData
from app.services.intake.upi import qr_png, upi_link
from app.services.mailer import Mailer


@pytest.fixture
def settings() -> Settings:
    return Settings(environment="test", smtp_password=None)


def _data() -> CaseData:
    return CaseData(name="Test Kumar", mobile="9531477342", services=["DPR_ANALYSIS"])


def test_the_link_matches_the_dashboards():
    # frontend/src/components/ui/UpiQr.tsx upiLink(): same fields, same order.
    assert upi_link("lasservices@upi", 1295, "LAS services 9531477342") == (
        "upi://pay?pa=lasservices@upi&pn=LAS%20Services&am=1295.00&cu=INR"
        "&tn=LAS%20services%209531477342"
    )
    assert "am=" not in upi_link("lasservices@upi", None, "LAS")  # payer types it


def test_the_qr_is_a_black_and_white_png():
    png = qr_png(upi_link("lasservices@upi", 500, "LAS"))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", png[16:24])
    assert width == height and 150 < width < 600


def test_the_registered_email_carries_the_qr_inline(settings):
    data = _data()
    mail = templates.registered(
        "ABCDEF1234",
        data,
        settings,
        services=data.chosen_services(settings),
        joining_fee=295,
        credentials_note="",
        problems=[],
    )
    assert [name for name, _ in mail.inline_images] == ["upiqr"]
    assert 'src="cid:upiqr"' in mail.html
    assert "Pay Rs 1,295 to" in mail.html  # services 1,000 + joining fee 295
    assert "am=1295.00" in mail.text and "UPI link:" in mail.text
    message = Mailer(settings)._build(dataclasses.replace(mail, to="bd@example.com"))
    images = [p for p in message.walk() if p.get_content_type() == "image/png"]
    assert [p["Content-ID"] for p in images] == ["<upiqr>"]
    assert images[0].get_payload(decode=True) == mail.inline_images[0][1]


def test_an_existing_customers_qr_has_no_amount_when_the_fee_is_unknown(settings):
    data = _data()
    mail = templates.registered(
        "ABCDEF1234",
        data,
        settings,
        services=data.chosen_services(settings),
        joining_fee=None,  # first payment unknown: the payer types the amount
        credentials_note="",
        problems=[],
    )
    assert "am=" not in mail.text and "the amount due" in mail.html


def test_the_report_due_emails_carry_the_qr_for_the_amount_owed(settings):
    customer = templates.report_due_customer(
        "Test Kumar", "DPR Analysis", 1295, settings
    )
    assert 'src="cid:upiqr"' in customer.html and "am=1295.00" in customer.text
    bd = templates.report_locked(
        "ABCDEF1234",
        "Test Kumar",
        "9531477342",
        settings,
        service_name="DPR Analysis",
        amount=1295,
    )
    assert 'src="cid:upiqr"' in bd.html and "LAS-PAID%20ABCDEF1234" in bd.html


def test_without_a_upi_id_there_is_no_qr():
    s = Settings(environment="test", smtp_password=None, intake_pay_now_upi_id=None)
    data = _data()
    mail = templates.registered(
        "ABCDEF1234",
        data,
        s,
        services=data.chosen_services(s),
        joining_fee=295,
        credentials_note="",
        problems=[],
    )
    assert mail.inline_images == () and "cid:upiqr" not in mail.html
    assert "once the customer has paid Rs 1,295, reply PAY NOW" in mail.text
