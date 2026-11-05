"""Conservative file signatures based on LAS_Email attachment MIME definitions."""

import io
import zipfile

PDF = "application/pdf"
JPEG = "image/jpeg"
PNG = "image/png"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV = "text/csv"
ALLOWED = {PDF, JPEG, PNG, DOCX, XLSX, CSV}


def sniff(data: bytes, filename: str) -> str:
    if data.startswith(b"%PDF-"):
        return PDF
    if data.startswith(b"\xff\xd8\xff"):
        return JPEG
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if data.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = set(z.namelist())
                if "[Content_Types].xml" in names:
                    if "word/document.xml" in names:
                        return DOCX
                    if any(n.startswith("xl/") for n in names):
                        return XLSX
        except (zipfile.BadZipFile, ValueError):
            pass
    if filename.lower().endswith(".csv") and b"\x00" not in data[:4096]:
        return CSV
    return "application/octet-stream"
