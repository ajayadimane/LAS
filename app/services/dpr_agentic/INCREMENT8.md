# Increment 8 — Phase B: deterministic financial verification

Phase B of the production-grade plan (Phase A: async pipeline + worker, see
INCREMENT7.md). Phases C-F -- vision fallback, PDF rendering, security
hardening, operational completeness -- are not done here.

## What changed

- **New `financial_schema.ProjectFinancials`**: cost-of-project components,
  means-of-finance components, and per-year cashflow figures (net profit,
  depreciation, interest, principal/interest repayment) -- every field
  optional, `None` means the document didn't state it, never an estimate.
- **New extraction step**: a dedicated Gemini call
  (`GeminiAgentClient.extract()`, its own `responseSchema` --
  `financial_extraction` is not a graph node and has nothing to do with the
  10 narrative roles) whose only job is copying numbers the document already
  states into `ProjectFinancials`. Runs once per document, before the
  narrative roles, in both `orchestrator.analyze()` (sequential) and
  `parallel_graph.analyze_parallel()` (the production LangGraph path).
- **New `financial_calc.py`** -- pure Python, no LLM: cost-of-project
  components sum to the stated total, means-of-finance components sum to
  its stated total, means-of-finance balances cost-of-project, and DSCR per
  year (`(net profit + depreciation + interest) / (principal + interest
  repayment)`). No scheme/policy pass-fail judgment -- only DSCR < 1.0 is
  flagged, since any scheme-specific threshold needs a real policy corpus
  this increment still doesn't have.
- **`Finding` gained `provenance`** (`"calculated"` | `"extracted"` |
  `"ai_interpreted"` default | `"assumption"` | `"unverified"`). A mismatch
  or notable DSCR is converted straight into a `Finding` in code
  (`financial_calc.findings_from_calculations`) with `provenance:
  "calculated"` and `verification: "source_verified"` -- it's always present
  in the result regardless of whether any narrative role mentions it, since
  it never depends on an LLM choosing to report it.
- **`finance`/`risk`/`verifier`/`report` prompts updated** to treat
  `calculated_facts` (the recomputed figures, passed in every role's
  context) as ground truth to build on, not re-derive or contradict.
- **Graceful degradation**: if the extraction call itself fails (bad
  document, model error), analysis still completes with the narrative roles
  -- no calculated findings, and an `unresolved_questions` entry saying so.
  Never aborts the whole run over one failed call.

## Verified

- 396 tests passing (was 379): 15 new `financial_calc.py` unit tests
  (balanced vs. mismatched cost/means-of-finance, healthy vs. sub-1.0 DSCR,
  zero-repayment and incomplete-year edge cases, empty-document no-op) plus
  2 orchestrator-wiring tests confirming a mismatch surfaces as a
  `provenance: "calculated"` finding and that extraction failure degrades
  gracefully rather than aborting.
- Every existing fake LLM test double (`test_dpr_agentic.py`,
  `test_dpr_parallel_graph.py`, `test_dpr_worker.py`,
  `test_dpr_report_access.py`) updated with a no-op `extract()`, confirming
  the new call doesn't change behavior for a document with no financial
  figures to extract.
- Fresh, hash-verified install passes the full suite (no new dependencies
  this phase).

## Still not done (next: Phase C)

- No vision/OCR fallback for a scanned document.
- No PDF rendering -- still structured JSON.
- Sector feasibility, policy/scheme compliance and market research remain
  unverified LLM judgment, same as before this increment.
