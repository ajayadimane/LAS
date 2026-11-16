"""Uploaded bytes -> classified, text-bearing pages ready for analysis.

The one implementation of "parse, recover weak pages with Gemini vision,
classify", shared by the worker (scripts/dpr_worker.py) and the evaluation
harness (scripts/dpr_eval.py), so an evaluation measures exactly what
production runs. No database, no job state: the caller decides what to do
with the pages or with a ClassificationNotReady.

The three document-pipeline callables are parameters, not imports used
directly, so a caller can substitute them -- the worker passes its own
module-level names, which its tests patch.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.core.logging import get_logger

from .document_pipeline.classify import classify_by_rules
from .document_pipeline.pipeline import (
    DocumentProcessingError,
    DocumentResult,
    inspect_document,
)
from .document_pipeline.vision import (
    extract_pages_via_vision,
    render_pages_to_images,
)
from .ocr_cache import OcrCache, cached_ocr

logger = get_logger(__name__)

DPR_MIN_CONFIDENCE = 0.8


class ClassificationNotReady(Exception):
    """The document isn't a confidently-classified, text-bearing DPR."""


class NeedsVisionFallback(ClassificationNotReady):
    """No usable extracted text, and vision OCR is off, failed, or still
    didn't produce a confident DPR classification."""


class ClassificationReviewRequired(ClassificationNotReady):
    pass


@dataclass(frozen=True)
class PreparedDocument:
    pages: list[dict]
    doc_class: str
    confidence: float
    mime: str
    # Page numbers recovered by Gemini vision (empty when none were needed).
    vision_pages: list[int] = field(default_factory=list)


async def prepare_document(
    data: bytes,
    filename: str,
    *,
    settings,
    gemini,
    liteparse=None,
    run_id: str | None = None,
    inspect: Callable[..., DocumentResult] = inspect_document,
    render: Callable[..., list[bytes]] = render_pages_to_images,
    ocr: Callable[..., Awaitable[list[dict]]] = extract_pages_via_vision,
    document_sha256: str | None = None,
    ocr_cache: OcrCache | None = None,
) -> PreparedDocument:
    """Raises ClassificationReviewRequired / NeedsVisionFallback (both
    ClassificationNotReady) when the document cannot be analysed as a DPR,
    and GeminiAgentError when vision OCR itself fails."""
    try:
        parsed = inspect(
            data,
            filename,
            liteparse=liteparse,
            page_min_chars=settings.dpr_agentic_liteparse_page_min_chars,
        )
    except DocumentProcessingError as exc:
        raise ClassificationReviewRequired(str(exc)) from exc
    if document_sha256 and ocr_cache is not None:
        # Same document analysed again: reuse the earlier vision text.
        ocr = cached_ocr(ocr, ocr_cache, document_sha256,
                         getattr(gemini, "model", None) or "unknown")

    pages, doc_class, confidence = parsed.pages, parsed.doc_class, parsed.confidence
    vision_pages: list[int] = []
    logger.info(
        "dpr_parse_summary run_id=%s parser=%s pages=%d weak_pages=%s "
        "vision_enabled=%s",
        run_id,
        "liteparse" if parsed.low_text_pages is not None else "liteparse_unavailable",
        len(pages),
        len(parsed.low_text_pages or []),
        settings.dpr_agentic_vision_enabled,
    )
    if parsed.needs_vision:
        if not settings.dpr_agentic_vision_enabled:
            raise NeedsVisionFallback("no usable extracted text; vision is disabled")
        if parsed.low_text_pages is None:
            # LiteParse itself was unavailable/failed -- no per-page signal
            # to target, so fall back across the whole document (bounded by
            # the vision page cap), same as before LiteParse.
            if parsed.mime == "application/pdf":
                import pymupdf

                with pymupdf.open(stream=data, filetype="pdf") as doc:
                    if doc.page_count > settings.dpr_agentic_vision_max_pages:
                        raise NeedsVisionFallback(
                            "LiteParse failed and full vision recovery exceeds "
                            "the page cap; refusing incomplete financial analysis"
                        )
            images = render(
                data, parsed.mime, max_pages=settings.dpr_agentic_vision_max_pages
            )
            if not images:
                raise NeedsVisionFallback(
                    "no usable extracted text and nothing to render"
                )
            pages = await ocr(gemini, images)
            vision_pages = [p["page"] for p in pages]
        else:
            # LiteParse succeeded overall; only recover the specific pages it
            # couldn't, keeping its text for every other page.
            images = render(
                data,
                parsed.mime,
                max_pages=settings.dpr_agentic_vision_max_pages,
                pages=parsed.low_text_pages,
            )
            if not images:
                raise NeedsVisionFallback(
                    "no usable extracted text and nothing to render"
                )
            if len(images) != len(parsed.low_text_pages):
                raise NeedsVisionFallback(
                    "Some weak pages exceeded the vision page limit"
                )
            recovered = await ocr(gemini, images, page_numbers=parsed.low_text_pages)
            recovered_by_page = {p["page"]: p["text"] for p in recovered}
            if any(
                not recovered_by_page.get(n, "").strip() for n in parsed.low_text_pages
            ):
                raise NeedsVisionFallback(
                    "Targeted Gemini OCR did not recover all required pages"
                )
            pages = [
                {"page": p["page"], "text": recovered_by_page.get(p["page"], p["text"])}
                for p in pages
            ]
            vision_pages = list(parsed.low_text_pages)
        preview = "\n".join(p["text"] for p in pages)
        if not preview.strip():
            raise NeedsVisionFallback("vision OCR produced no usable text")
        reclassified = classify_by_rules(filename, parsed.mime, preview[:30000])
        doc_class, confidence = reclassified.doc_class, reclassified.confidence

    if doc_class != "DPR" or confidence < DPR_MIN_CONFIDENCE:
        raise ClassificationReviewRequired(
            f"classified as {doc_class} ({confidence:.2f})"
        )
    return PreparedDocument(
        pages=[p for p in pages if p["text"].strip()],
        doc_class=doc_class,
        confidence=confidence,
        mime=parsed.mime,
        vision_pages=vision_pages,
    )
