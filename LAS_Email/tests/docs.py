"""Build small real documents for tests."""

from __future__ import annotations

import io
import zipfile


def pdf(
    text: str = "Detailed Project Report\nCost of project Rs 5,00,000\nMeans of finance",
) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data: bytes = doc.tobytes()
    doc.close()
    return data


def locked_pdf() -> bytes:
    import pikepdf

    out = io.BytesIO()
    with pikepdf.open(io.BytesIO(pdf())) as doc:
        doc.save(out, encryption=pikepdf.Encryption(owner="secret", user="secret", R=6))
    return out.getvalue()


def png(text_marker: bytes = b"") -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buf, "PNG")
    return buf.getvalue() + text_marker


def zip_of(files: dict[str, bytes], compress: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compress) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def xlsx(rows: list[list[object]]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
