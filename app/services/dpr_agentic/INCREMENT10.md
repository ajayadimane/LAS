# Increment 10 — Phase D: PDF report rendering

Phase D of the production-grade plan (Phase A: async pipeline + worker,
INCREMENT7.md; Phase B: deterministic financial verification, INCREMENT8.md;
Phase C: vision fallback, INCREMENT9.md). Phases E-F -- security hardening,
operational completeness -- are not done here.

## What changed

- **New `report_render.py`** (new dependency: `reportlab`, pure Python, no
  system libraries unlike weasyprint): renders a completed `AnalysisResult`
  into a PDF -- metadata (service request, wallet document id, SHA-256,
  status), the disclaimer, a dedicated **Calculated Figures** section for
  every `provenance: "calculated"` finding (labelled "code-verified, not AI
  judgment"), the remaining findings grouped under **Findings** and sorted
  most severe first, and **Open Questions**. Pure rendering: never computes,
  infers or adds anything the JSON doesn't already contain.
- **`GET .../report` gained `?format=pdf`** on both the customer and BD
  routes, reusing `access_report()`'s gate completely unchanged -- the same
  404/503/403 rules apply first; a locked report is never rendered, and
  there is no separate authorization path to keep in sync.
- **No new storage.** The PDF is generated fresh from the stored JSON on
  every request, never cached or persisted itself -- the JSON stays the
  single source of truth, so a PDF can never drift from it. No new
  migration, no new column.

## Verified

- 418 tests passing (was 408): 7 `report_render.py` unit tests that verify
  actual rendered *content* by extracting text back out of the generated PDF
  with pymupdf (already a project dependency) -- not just that a PDF opens.
  Covers the disclaimer and metadata appearing, a calculated finding landing
  in the right section labelled code-verified, an AI-interpreted finding
  landing in the other section with its evidence quote and page number,
  severity-based ordering, and a clean empty-report render. Plus 3 HTTP-level
  tests (customer and BD routes) confirming `?format=pdf` returns a real PDF
  only once the same gate that releases the JSON would also release it.
- Fresh, hash-verified install passes the full suite; `reportlab` added and
  locked.

## Still not done (next: Phase E)

- No prompt-injection test suite.
- No per-customer rate limit on new analysis submissions.
- No audit confirming page text/raw Gemini responses never reach logs.
- `.env.example`, per-run cost/timing logging, and the pre-existing lint
  debt noted since Increment 6 remain Phase F.
