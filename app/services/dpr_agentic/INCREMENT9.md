# Increment 9 — Phase C: vision fallback for scanned documents

Phase C of the production-grade plan (Phase A: async pipeline + worker,
INCREMENT7.md; Phase B: deterministic financial verification, INCREMENT8.md).
Phases D-F -- PDF rendering, security hardening, operational completeness --
are not done here.

## What changed

- **`GeminiAgentClient` gained `ocr_page()`** and a generalized
  `_call_with_parts()` the existing `generate()`/`extract()` now share with
  it -- `ocr_page()` sends one page image (`inline_data`) instead of a text
  context, with its own strict system prompt ("transcribe, never
  summarize/interpret/add text") and its own tiny `{"text": ...}` schema.
  The same client, API key and model already configured for narrative
  analysis handle OCR too -- `gemini-2.5-flash` is multimodal; no separate
  vision model setting.
- **New `document_pipeline/vision.py`**: `render_pages_to_images()`
  (pymupdf `get_pixmap()`, PDF or a bare image file) and
  `extract_pages_via_vision()` (one Gemini call per image, assembled into
  the exact same page/text shape `inspect_document()` already produces --
  classification, the LangGraph pipeline and evidence verification need
  zero changes downstream). Kept out of `pipeline.py` on purpose:  that
  module is synchronous and pure (no network); this one needs an event
  loop.
- **New `DPR_AGENTIC_VISION_ENABLED`** (off by default -- a separate
  decision from `DPR_AGENTIC_ENABLED`, since vision sends page images, not
  just text, and costs more per page) and **`DPR_AGENTIC_VISION_MAX_PAGES`**
  (default 20, independent of the ordinary text pipeline's page cap).
- **`scripts/dpr_worker.py`'s `load_input()`** now recovers instead of
  dead-ending on `needs_vision`: renders pages, OCRs them, **re-classifies
  the recovered text** with the same rule-based classifier the ordinary
  path uses (a scanned non-DPR document, e.g. a scanned PAN card, must not
  be waved through just because vision successfully read some text off it),
  and only then proceeds if it's now confidently a DPR. Any failure along
  the way (vision disabled, nothing to render, blank OCR, still not a
  confident DPR) is still a clean `FAILED` run with a specific error code,
  never a silent partial result.

## Verified

- 408 tests passing (was 396): 7 unit tests for `vision.py` (real multi-page
  PDF rendering via pymupdf, page cap, image-passthrough, unsupported mime,
  OCR-per-image ordering, a failed page failing the whole extraction rather
  than silently producing a gap) plus 5 worker-level integration tests
  against the real Postgres fixtures covering vision disabled, successful
  recovery, blank OCR, OCR'd-but-not-a-DPR, and unrenderable document.
- Fresh, hash-verified install passes the full suite -- no new dependencies
  this phase (pymupdf was already there for the ordinary text path).

## Still not done (next: Phase D)

- No PDF rendering -- the report is still structured JSON.
- The internal `/inspect-upload` debug endpoint and the now-effectively-dead
  `live_upload.py` module (nothing in the live API calls it after Phase A)
  still dead-end on a scanned document; only the worker path got vision
  recovery. Worth a cleanup pass in Phase F, not done here to keep this
  increment's diff to the stated scope.
- No lease renewal; a vision-heavy run (more Gemini calls than the
  text-only path) is more likely to approach the worker's 1800s lease on a
  very large scanned document.
