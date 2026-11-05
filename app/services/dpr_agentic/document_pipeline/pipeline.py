"""Stateless upload inspection. Does not store DPRs or bypass Wallet."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256

from app.core.logging import get_logger

from .classify import classify_by_rules
from .liteparse_client import LiteParseClient, LiteParseUnavailable
from .sniff import ALLOWED, JPEG, PDF, PNG, sniff
from .text import extract_text

logger = get_logger(__name__)


class DocumentProcessingError(ValueError):
    pass


@dataclass(frozen=True)
class DocumentResult:
    sha256: str
    mime: str
    doc_class: str
    confidence: float
    classification_method: str
    pages: list[dict]
    needs_vision: bool
    # Which page numbers still need a Gemini-vision fallback after LiteParse's
    # own (local, no-network) text/OCR pass -- None means LiteParse itself was
    # unavailable or failed outright, so the caller must fall back for the
    # *whole* document (see scripts/dpr_worker.py); a (possibly empty) list
    # means LiteParse succeeded and only these specific pages are still weak.
    low_text_pages: list[int] | None = field(default_factory=list)


def _parse_with_liteparse(
    data: bytes,
    liteparse: LiteParseClient | None,
    max_pages: int,
    page_min_chars: int,
) -> tuple[list[dict], list[int] | None]:
    client = liteparse or LiteParseClient()
    try:
        lp_pages = client.parse(data, max_pages=max_pages)
    except LiteParseUnavailable as exc:
        logger.warning("dpr_liteparse_parse_failed reason=%s", exc)
        return [], None
    pages: list[dict] = []
    low_text_pages: list[int] = []
    for p in lp_pages:
        text = p.text or ""
        is_weak = len(text.strip()) < page_min_chars or (
            p.confidence is not None and p.confidence < 0.5
        )
        if is_weak:
            low_text_pages.append(p.page)
        pages.append({"page": p.page, "text": text})
    return pages, low_text_pages


def inspect_document(
    data: bytes,
    filename: str,
    *,
    max_bytes: int = 20_000_000,
    max_pages: int = 100,
    liteparse: LiteParseClient | None = None,
    page_min_chars: int = 50,
) -> DocumentResult:
    if not data or len(data) > max_bytes:
        raise DocumentProcessingError("Empty or oversized document")
    mime = sniff(data, filename)
    if mime not in ALLOWED:
        raise DocumentProcessingError("Unsupported document type")
    pages: list[dict] = []
    low_text_pages: list[int] | None = []
    if mime == PDF:
        try:
            import pymupdf
        except ImportError as exc:
            raise DocumentProcessingError(
                "PDF parser unavailable: install PyMuPDF"
            ) from exc
        try:
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                if doc.needs_pass:
                    raise DocumentProcessingError("Password-protected PDF")
                if doc.page_count > max_pages:
                    raise DocumentProcessingError("Too many pages")
        except DocumentProcessingError:
            raise
        except Exception as exc:
            raise DocumentProcessingError("Malformed PDF") from exc
        pages, low_text_pages = _parse_with_liteparse(
            data, liteparse, max_pages, page_min_chars
        )
    elif mime in (JPEG, PNG):
        pages, low_text_pages = _parse_with_liteparse(
            data, liteparse, max_pages, page_min_chars
        )
    else:
        try:
            content = extract_text(data, mime)
        except Exception as exc:
            raise DocumentProcessingError("Document extraction failed") from exc
        pages = [{"page": 1, "text": content}]
    preview = "\n".join(p["text"] for p in pages)
    classification = classify_by_rules(filename, mime, preview[:30000])
    # Whole-document fallback (LiteParse unavailable) or specific pages still
    # weak after LiteParse's own local OCR pass -- either means Gemini vision
    # recovery must run before analysis.
    needs_vision = mime in (PDF, JPEG, PNG) and (
        low_text_pages is None or bool(low_text_pages)
    )
    return DocumentResult(
        sha256(data).hexdigest(),
        mime,
        classification.doc_class,
        classification.confidence,
        classification.method,
        pages,
        needs_vision,
        low_text_pages,
    )
