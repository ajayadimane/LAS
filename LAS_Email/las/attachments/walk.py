"""Walk a MIME tree (nested .eml, zip archives) into candidate files, with hard limits."""

from __future__ import annotations

import base64
import io
import quopri
import zipfile
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser

from las.ingest.mime import extract_body


@dataclass(frozen=True)
class RawFile:
    filename: str
    declared_mime: str | None
    data: bytes
    origin: str  # 'mail', 'nested:<n>', 'zip:<name>'


@dataclass(frozen=True)
class Rejected:
    filename: str
    reason: str


@dataclass
class WalkResult:
    files: list[RawFile] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)
    nested_texts: list[str] = field(default_factory=list)
    # Shared across every (possibly nested) zip in this mail, so the archive-size budget can't be
    # multiplied by nesting multiple zips inside each other or side by side.
    zip_bytes_seen: int = 0


@dataclass(frozen=True)
class Limits:
    max_depth: int = 3
    max_count: int = 25
    max_file_bytes: int = 20 * 1024 * 1024
    max_zip_total: int = 60 * 1024 * 1024
    max_zip_ratio: int = 100


def _is_zip(filename: str, data: bytes) -> bool:
    if not data.startswith(b"PK\x03\x04"):
        return False
    lower = filename.lower()
    if lower.endswith((".docx", ".xlsx")):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        return False
    return "[Content_Types].xml" not in names  # OOXML documents are zips too


def _walk_zip(name: str, data: bytes, depth: int, lim: Limits, out: WalkResult) -> None:
    if depth > lim.max_depth:
        out.rejected.append(Rejected(name, "archive nested too deeply"))
        return
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        out.rejected.append(Rejected(name, "corrupt zip archive"))
        return
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            member = info.filename.rsplit("/", 1)[-1] or info.filename
            if info.flag_bits & 0x1:
                out.rejected.append(Rejected(f"{name}/{member}", "password-protected archive"))
                continue
            if len(out.files) >= lim.max_count:
                out.rejected.append(Rejected(f"{name}/{member}", "too many files"))
                continue
            if info.file_size > lim.max_file_bytes:
                out.rejected.append(Rejected(member, "larger than 20 MB"))
                continue
            if info.compress_size and info.file_size / info.compress_size > lim.max_zip_ratio:
                out.rejected.append(Rejected(member, "suspicious compression ratio"))
                continue
            out.zip_bytes_seen += info.file_size
            if out.zip_bytes_seen > lim.max_zip_total:
                out.rejected.append(Rejected(member, "archive too large"))
                break
            content = zf.read(info)
            if _is_zip(member, content):
                _walk_zip(member, content, depth + 1, lim, out)
            else:
                out.files.append(RawFile(member, None, content, f"zip:{name}"))


def _leaf_parts(m: Message) -> list[Message]:
    """Leaves of the MIME tree, stopping at attached mails (handled by recursion)."""
    if m.get_content_type() == "message/rfc822":
        return [m]
    if m.is_multipart():
        out: list[Message] = []
        for p in m.get_payload():
            if isinstance(p, Message):
                out += _leaf_parts(p)
        return out
    return [m]


def _walk_message(msg: Message, depth: int, lim: Limits, out: WalkResult, origin: str) -> None:
    parts = _leaf_parts(msg) if msg.is_multipart() else []
    for part in parts:
        if part.get_content_type() == "message/rfc822":
            payload = part.get_payload()
            inner = payload[0] if isinstance(payload, list) and payload else None
            cte = str(part.get("Content-Transfer-Encoding", "")).strip().lower()
            if isinstance(inner, Message) and cte in ("base64", "quoted-printable"):
                # Non-conformant but common: the attached mail itself is transfer-encoded.
                encoded = inner.get_payload()
                if isinstance(encoded, str):
                    raw = (
                        base64.b64decode(encoded)
                        if cte == "base64"
                        else quopri.decodestring(encoded.encode())
                    )
                    inner = BytesParser(policy=policy.default).parsebytes(raw)
            if not isinstance(inner, Message):
                continue
            if depth + 1 > lim.max_depth:
                out.rejected.append(
                    Rejected(
                        part.get_filename() or "attached mail", "attached mail nested too deeply"
                    )
                )
                continue
            if isinstance(inner, EmailMessage):
                text = extract_body(inner)
                if text:
                    subj = inner.get("Subject", "")
                    out.nested_texts.append(f"[attached mail: {subj}]\n{text}")
            _walk_message(inner, depth + 1, lim, out, f"nested:{depth + 1}")
            continue
        filename = part.get_filename()
        disposition = part.get_content_disposition()
        maintype = part.get_content_maintype()
        if not filename and (disposition != "attachment") and maintype == "text":
            continue  # body text
        if not filename and maintype not in ("image", "application"):
            continue
        if part.get("Content-ID") and disposition != "attachment":
            continue  # inline asset referenced via cid: (e.g. a signature logo), not a document
        data = part.get_payload(decode=True)
        if not isinstance(data, bytes):
            continue
        name = filename or f"inline.{part.get_content_subtype()}"
        if len(out.files) >= lim.max_count:
            out.rejected.append(Rejected(name, "too many files"))
            continue
        if len(data) > lim.max_file_bytes:
            out.rejected.append(Rejected(name, "larger than 20 MB"))
            continue
        if _is_zip(name, data):
            _walk_zip(name, data, 1, lim, out)
            continue
        out.files.append(RawFile(name, part.get_content_type(), data, origin))


def walk_attachments(msg: Message, lim: Limits) -> WalkResult:
    out = WalkResult()
    _walk_message(msg, 0, lim, out, "mail")
    return out
