"""Gemini-vision OCR fallback for a scanned/image-only document.

Only used by scripts/dpr_worker.py when pipeline.inspect_document() flags
needs_vision: ordinary text extraction found nothing usable. Produces plain
page text in exactly the shape the rest of the pipeline already consumes
(page/text pairs) -- classification, orchestration and evidence
verification downstream need no changes at all for this.

Kept separate from pipeline.py: that module is synchronous and pure (no
network); this one makes real Gemini calls and needs an event loop.
"""

from __future__ import annotations

from ..gemini import CURRENT_ROLE
from .sniff import JPEG, PDF, PNG


def render_pages_to_images(
    data: bytes,
    mime: str,
    *,
    max_pages: int,
    dpi: int = 150,
    pages: list[int] | None = None,
) -> list[bytes]:
    """PNG bytes for the requested pages.

    `pages` (1-based) renders exactly those pages, in that order -- used for a
    targeted fallback onto the specific pages document_pipeline/pipeline.py's
    LiteParse pass couldn't recover. Without it (`pages=None`), renders the
    document's first `max_pages` pages -- the whole-document fallback used
    when LiteParse itself is unavailable; pages beyond the cap are never
    rendered or sent anywhere, since vision calls cost more per page than
    text."""
    if mime in (JPEG, PNG):
        return [data]
    if mime != PDF:
        return []
    import pymupdf

    images = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        if pages is not None:
            targets = [p - 1 for p in pages if 1 <= p <= doc.page_count]
        else:
            targets = list(range(min(doc.page_count, max_pages)))
        for i in targets:
            pix = doc[i].get_pixmap(dpi=dpi)
            images.append(pix.tobytes("png"))
    return images


async def extract_pages_via_vision(gemini, images: list[bytes]) -> list[dict]:
    """One Gemini call per image, not batched, to bound each request's size.

    Any page's OCR failure fails the whole call: partial text would mean
    analyzing an incomplete understanding of the document without any
    visible signal, so the caller gets a clean failure to retry instead
    (see scripts/dpr_worker.py, which already treats this like any other
    analysis failure)."""
    role_token = CURRENT_ROLE.set("vision_ocr")
    try:
        pages = []
        for i, image in enumerate(images, start=1):
            text = await gemini.ocr_page(image)
            pages.append({"page": i, "text": text})
        return pages
    finally:
        CURRENT_ROLE.reset(role_token)
