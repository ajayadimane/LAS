"""Magic-byte type detection and the attachment allow-list."""

from __future__ import annotations

import io
import zipfile

import magic

PDF = "application/pdf"
JPEG = "image/jpeg"
PNG = "image/png"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV = "text/csv"
ALLOWED = frozenset({PDF, JPEG, PNG, DOCX, XLSX, CSV})


def _ooxml_kind(data: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile:
        return None
    if "[Content_Types].xml" not in names:
        return None
    if any(n.startswith("word/") for n in names):
        return DOCX
    if any(n.startswith("xl/") for n in names):
        return XLSX
    return None


def sniff(data: bytes, filename: str) -> str:
    detected = str(
        magic.from_buffer(data[:65536] if not data.startswith(b"PK") else data, mime=True)
    )
    if detected in ("application/zip", "application/octet-stream") or detected.startswith(
        "application/vnd.openxmlformats"
    ):
        return _ooxml_kind(data) or detected
    if detected in ("text/plain", "text/csv", "application/csv") and filename.lower().endswith(
        ".csv"
    ):
        return CSV
    return detected


def is_allowed(mime: str) -> bool:
    return mime in ALLOWED
