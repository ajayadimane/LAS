"""Deterministic context assembly for interpretation.

Produces the new text (quoted history stripped, forwarded content kept), the quoted history (for
reference/grounding only), the BD's earlier mails in the same thread, the attachments' text and the
service catalog. The mail body is untrusted and is passed to the model inside delimiters as data.
Cases, payments and replies live in Core LAS, so there is no "open work" to summarise here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from las.common.models import (
    EmailAttachment,
    InboundEmail,
    ServiceCatalog,
    ServiceSynonym,
)

_LINE_MARKERS = [
    re.compile(r"^\s*On\s.{5,200}\bwrote:\s*$", re.IGNORECASE),
    re.compile(r"^\s*-{2,}\s*On\s.+wrote\s*-{2,}\s*$", re.IGNORECASE),  # Zoho
    re.compile(r"^\s*-{3,}\s*Original Message\s*-{3,}\s*$", re.IGNORECASE),
    re.compile(r"^\s*_{20,}\s*$"),
    re.compile(r"^.{0,160}(ने लिखा|ಬರೆದಿದ್ದಾರೆ)\s*:\s*$"),
]
_TWO_LINE_MARKERS = [
    (
        re.compile(r"^\s*On\s.{5,200}$", re.IGNORECASE),
        re.compile(r"^.{0,200}\bwrote:\s*$", re.IGNORECASE),
    ),
    (
        re.compile(r"^\s*From:\s.+$", re.IGNORECASE),
        re.compile(r"^\s*(Sent|Date):\s.+$", re.IGNORECASE),
    ),
]
# Outlook/Zoho-style quoted header block: "From:" followed, within a few lines (To:/Cc: may sit
# in between, in any order the mail client chooses), by both a Sent/Date line and a Subject line.
_HEADER_BLOCK_FROM = re.compile(r"^\s*From:\s.+$", re.IGNORECASE)
_HEADER_BLOCK_DATE = re.compile(r"^\s*(Sent|Date):\s.+$", re.IGNORECASE)
_HEADER_BLOCK_SUBJECT = re.compile(r"^\s*Subject:\s.+$", re.IGNORECASE)
_HEADER_BLOCK_WINDOW = 5
_FORWARD_MARKER = re.compile(
    r"^\s*(-{5,}\s*Forwarded message\s*-{5,}|Begin forwarded message:|-{3,}\s*Forwarded Message\s*-{3,})",
    re.IGNORECASE,
)
_SIGNATURE = re.compile(r"^--\s*$")


@dataclass
class SplitBody:
    new_text: str
    quoted_text: str
    forwarded: bool


def _reply_cut(lines: list[str]) -> int | None:
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith(">") or any(rx.match(ln) for rx in _LINE_MARKERS):
            return i
        if i + 1 < len(lines) and any(
            a.match(ln) and b.match(lines[i + 1]) for a, b in _TWO_LINE_MARKERS
        ):
            return i
        if _HEADER_BLOCK_FROM.match(ln):
            window = lines[i + 1 : i + 1 + _HEADER_BLOCK_WINDOW]
            if any(_HEADER_BLOCK_DATE.match(w) for w in window) and any(
                _HEADER_BLOCK_SUBJECT.match(w) for w in window
            ):
                return i
    return None


def split_quoted(body: str) -> SplitBody:
    """New text vs quoted history. A forwarded mail keeps the forwarded part: it is the request."""
    lines = body.replace("\r\n", "\n").split("\n")
    fwd = next((i for i, ln in enumerate(lines) if _FORWARD_MARKER.match(ln)), None)
    cut = _reply_cut(lines)
    if fwd is not None and (cut is None or fwd <= cut):
        kept = [ln for ln in lines if not ln.lstrip().startswith(">")]
        return SplitBody("\n".join(kept).strip(), "", True)
    new_lines, quoted_lines = (lines, []) if cut is None else (lines[:cut], lines[cut:])
    sig = next((i for i, ln in enumerate(new_lines) if _SIGNATURE.match(ln)), None)
    if sig is not None:
        new_lines = new_lines[:sig]
    new, quoted = "\n".join(new_lines).strip(), "\n".join(quoted_lines).strip()
    if not new and quoted:
        new, quoted = quoted, ""
    return SplitBody(new, quoted, False)


# --- thread context -----------------------------------------------------------------------------
@dataclass
class AttachmentInfo:
    attachment_id: int
    filename: str
    mime: str
    doc_class: str | None
    text: str
    is_password_protected: bool
    av_status: str


@dataclass
class MailContext:
    subject: str
    new_text: str
    quoted_text: str
    forwarded: bool
    attachments: list[AttachmentInfo] = field(default_factory=list)
    thread_summary: str = ""
    catalog: list[ServiceCatalog] = field(default_factory=list)
    synonyms: dict[str, list[str]] = field(default_factory=dict)

    def source_text(self) -> str:
        """Everything a value may be grounded in: mail (new + quoted) and attachment text."""
        parts = [self.subject, self.new_text, self.quoted_text]
        parts += [f"{a.filename}\n{a.text}" for a in self.attachments]
        return "\n".join(p for p in parts if p)

    def name_source_text(self) -> str:
        """Like source_text(), but excludes attachment filenames. A BD can name a file anything,
        and the model may just echo a filename back as a guessed customer name; grounding that
        guess against the same filename proves nothing, so a customer's name must come from what
        was actually written or the document's own content instead."""
        parts = [self.subject, self.new_text, self.quoted_text, *(a.text for a in self.attachments)]
        return "\n".join(p for p in parts if p)


async def load_catalog(session: AsyncSession) -> tuple[list[ServiceCatalog], dict[str, list[str]]]:
    catalog = list(
        (
            await session.execute(
                select(ServiceCatalog)
                .where(ServiceCatalog.is_active)
                .order_by(ServiceCatalog.menu_order)
            )
        ).scalars()
    )
    synonyms: dict[str, list[str]] = {}
    for syn in (await session.execute(select(ServiceSynonym))).scalars():
        synonyms.setdefault(syn.service_code, []).append(syn.phrase)
    return catalog, synonyms


async def thread_summary(session: AsyncSession, inbound: InboundEmail, limit: int = 3) -> str:
    """The BD's earlier mails in this thread (by In-Reply-To / References), newest last."""
    ids = [i for i in [inbound.in_reply_to, *(inbound.references_hdr or "").split()] if i]
    if not ids:
        return "not a reply"
    earlier = (
        await session.execute(
            select(InboundEmail.subject, InboundEmail.body_text)
            .where(
                InboundEmail.message_id.in_(ids),
                InboundEmail.inbound_id != inbound.inbound_id,
            )
            .order_by(InboundEmail.received_at.desc())
            .limit(limit)
        )
    ).all()
    parts = ["a reply; earlier BD mails in this thread follow" if earlier else "a reply"]
    for e in reversed(earlier):
        snippet = " ".join(split_quoted(e.body_text or "").new_text.split())[:300]
        parts.append(f"earlier BD message '{e.subject or ''}': {snippet}")
    return "\n".join(parts)


async def load_attachments(
    session: AsyncSession, inbound_id: int, texts: dict[int, str]
) -> list[AttachmentInfo]:
    rows = (
        (
            await session.execute(
                select(EmailAttachment)
                .where(EmailAttachment.inbound_id == inbound_id)
                .order_by(EmailAttachment.attachment_id)
            )
        )
        .scalars()
        .all()
    )
    return [
        AttachmentInfo(
            a.attachment_id,
            a.original_filename,
            a.detected_mime,
            a.doc_class,
            texts.get(a.attachment_id, ""),
            a.is_password_protected,
            a.av_status,
        )
        for a in rows
    ]


async def build_context(
    session: AsyncSession, inbound: InboundEmail, attachment_texts: dict[int, str]
) -> MailContext:
    assert inbound.bd_id is not None
    split = split_quoted(inbound.body_text or "")
    catalog, synonyms = await load_catalog(session)
    return MailContext(
        subject=inbound.subject or "",
        new_text=split.new_text,
        quoted_text=split.quoted_text,
        forwarded=split.forwarded,
        attachments=await load_attachments(session, inbound.inbound_id, attachment_texts),
        thread_summary=await thread_summary(session, inbound),
        catalog=catalog,
        synonyms=synonyms,
    )
