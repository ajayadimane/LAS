"""Deterministic text extraction per type (OCR is decided by the pipeline
and metered there)."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass

from .sniff import CSV, DOCX, PDF, XLSX


@dataclass(frozen=True)
class PdfInfo:
    needs_password: bool
    page_count: int | None
    text: str


def pdf_info(data: bytes, max_pages: int = 5) -> PdfInfo:
    import pymupdf

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")  # type: ignore[no-untyped-call]
    except Exception:  # noqa: BLE001 - corrupt PDF
        return PdfInfo(False, None, "")
    with doc:
        if doc.needs_pass:
            return PdfInfo(True, doc.page_count, "")
        texts = [doc[i].get_text() for i in range(min(doc.page_count, max_pages))]  # type: ignore[no-untyped-call]
        return PdfInfo(False, doc.page_count, "\n".join(texts))


def docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


def read_table(data: bytes, mime: str, max_rows: int = 200) -> list[list[str]]:
    """First sheet (XLSX) or CSV as rows of strings, exactly as the cells read."""
    rows: list[list[str]] = []
    if mime == XLSX:
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        for r in ws.iter_rows(values_only=True):
            cells = [
                ""
                if v is None
                else (
                    str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)
                )
                for v in r
            ]
            if any(c.strip() for c in cells):
                rows.append(cells)
            if len(rows) >= max_rows:
                break
        wb.close()
    elif mime == CSV:
        text = data.decode("utf-8-sig", "replace")
        for r in csv.reader(io.StringIO(text)):
            if any(c.strip() for c in r):
                rows.append(r)
            if len(rows) >= max_rows:
                break
    return rows


def table_text(rows: list[list[str]], limit: int = 60) -> str:
    return "\n".join(" | ".join(c.strip() for c in r) for r in rows[:limit])


def extract_text(data: bytes, mime: str) -> str:
    if mime == PDF:
        return pdf_info(data).text
    if mime == DOCX:
        return docx_text(data)
    if mime in (XLSX, CSV):
        return table_text(read_table(data, mime))
    return ""
