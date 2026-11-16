"""walk_attachments must skip inline assets (e.g. a signature logo referenced via cid:) and
still pick up genuine attached documents."""

from __future__ import annotations

from email import policy
from email.message import EmailMessage
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.parser import BytesParser

from las.attachments.walk import Limits, walk_attachments


def _mail_with_inline_logo_and_attachment() -> EmailMessage:
    """Mirrors what a real signature-image mail looks like on the wire: a logo part carrying a
    Content-ID (referenced from the HTML signature via cid:) alongside a genuine PDF attachment.
    """
    msg = MIMEMultipart("mixed")
    msg["From"] = "bd@example.com"
    msg["To"] = "las@integramicro.co.in"
    msg["Subject"] = "customer on board"
    msg.attach(MIMEText("customer name: sanjay\nmob no: 9731566743", "plain"))

    logo = MIMEImage(b"\x89PNG\r\n\x1a\nlogo-bytes", _subtype="png")
    logo.add_header("Content-ID", "<logo123>")
    logo.add_header("Content-Disposition", "inline", filename="image001.png")
    msg.attach(logo)

    pdf_att = MIMEApplication(b"%PDF-1.4 dpr bytes", _subtype="pdf")
    pdf_att.add_header("Content-Disposition", "attachment", filename="dpr.pdf")
    msg.attach(pdf_att)

    parsed = BytesParser(policy=policy.default).parsebytes(msg.as_bytes())
    assert isinstance(parsed, EmailMessage)
    return parsed


def test_inline_signature_logo_is_not_treated_as_an_attachment() -> None:
    result = walk_attachments(_mail_with_inline_logo_and_attachment(), Limits())
    names = [f.filename for f in result.files]
    assert names == ["dpr.pdf"]
