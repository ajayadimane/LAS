"""The real LiteParse adapter (local, no network): table rows, locked PDFs, image OCR, metering."""

import io

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from las.attachments.parser import LiteParseParser
from las.common.db import SessionFactory
from las.common.deps import Deps
from las.common.llm import Attribution
from las.common.models import AiUsage
from tests.docs import locked_pdf
from tests.helpers import rows

PARSER = LiteParseParser(ocr_language="eng")


def _table_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    for i, (a, b) in enumerate(
        [("Particulars", "Amount"), ("Machinery", "3,50,000"), ("Total cost", "5,50,000")]
    ):
        page.insert_text((72, 100 + i * 20), a)
        page.insert_text((300, 100 + i * 20), b)
    data: bytes = doc.tobytes()
    return data


async def test_table_rows_stay_on_one_line() -> None:
    parsed = await PARSER.parse(_table_pdf(), "application/pdf")
    lines = [" ".join(ln.split()) for ln in parsed.text.splitlines()]
    assert "Machinery 3,50,000" in lines and "Total cost 5,50,000" in lines
    assert parsed.pages == 1 and parsed.provider == "liteparse"


async def test_password_protected_pdf_is_flagged() -> None:
    parsed = await PARSER.parse(locked_pdf(), "application/pdf")
    assert parsed.password_required and parsed.text == ""


async def test_image_is_ocrd_and_metered(deps: Deps, db: SessionFactory) -> None:
    img = Image.new("RGB", (900, 160), "white")
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
    except OSError:
        pytest.skip("DejaVu font not installed")
    ImageDraw.Draw(img).text((20, 50), "UTR 412345678901 Rs 2,708.10", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    parsed = await deps.llm.parse_document(
        PARSER, buf.getvalue(), "image/png", attribution=Attribution(bd_id=1)
    )
    assert "412345678901" in parsed.text
    (u,) = await rows(db, AiUsage)
    assert (u.purpose, u.provider, u.units, u.status) == ("OCR", "liteparse", 1, "SUCCESS")
