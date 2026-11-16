# Core LAS DPR CAM population + parsing/cost audit (5 November 2026)

## Source comparison

Baseline: uploaded `LAS(1).zip`. Report compared against `CAMreportprod1.pdf` and `LAS Customer CAM Report Template.pdf`.

## Verified root causes

1. `report_store.py` rendered the CAM from `AnalysisResult` inside an isolated subprocess. The `cam_adapter` had no populated borrower/project profile because those fields did not exist in the structured Document Agent output. This produced `Borrower name not yet confirmed`, blank project and branch details despite names appearing in *narrative* findings. New DPR-grounded identity fields now survive `AnalysisResult` and PDF subprocess serialization. Wallet identity should still take precedence when Core LAS supplies it.
2. `Settings.cam_engine_enabled` defaulted to `False`; `cam_adapter` returned empty project cost/funding/loan/scenario output unless the optional engine was explicitly enabled. New runs default to enabled (environment override remains possible). The already-paid financial extraction output now persists into `AnalysisResult` and populates generic cost, financing, P&L and repayment sections across sectors even if the harvester engine cannot compute a special scenario.
3. `GeminiAgentClient.FINANCIAL_SCHEMA` did not expose most of the `ProjectFinancials` model fields (repayment table, loan terms, capex context, operational details, PBT/tax). The LLM could not emit those fields even though the prompt requested them. Its schema has been expanded.
4. `parallel_graph.invoke` forwarded all extracted pages to each role; 10 role calls plus financial extraction repeatedly resubmitted the same document. Only the Document Agent and numeric extraction see the complete text now. Other roles receive relevant cited sections or upstream findings. Un-grounded Research and SBI Policy model calls are skipped while there are no actual retrieval tools; deterministic `UNVERIFIED` outputs replace them. The duplicate report-writing model call is also removed; structured CAM rendering is deterministic.
5. Exact title-based deduplication allowed synonymous issues to appear several times (the supplied report repeated the interest-omission issue four times). It now uses conservative issue-topic keys plus within-page/category title similarity. All findings remain available in JSON; the six-page borrower PDF deliberately shows only selected top issues, with the remainder available in the findings register/annexures.
6. LiteParse is attempted for digital AND scanned PDFs; it uses local OCR. Gemini Vision is only used for low-text / low-confidence pages when enabled, or the entire document when LiteParse fails. Intentionally blank PDF pages are no longer flagged solely for being blank; missing parser pages are tracked. Targeted vision keeps original page numbers. Incomplete > vision-page-limit full-document fallbacks are blocked instead of producing deceptively complete reports.
7. WeasyPrint's executive summary split onto page two and the raw list of 57 issues expanded a supposed six-page CAM into 14 pages. Summary spacing has been compacted; full findings are limited in the borrower PDF, with retained full detail in JSON. A 55-finding offline fixture rendered exactly six A4 pages.
8. Source reference in header included a Wallet person/mobile identifier. Display report ID now uses a stable non-identifying SHA-256 prefix, without changing database keys.
9. Back-ended / conditional subsidies are not counted as automatically available upfront financing.

## Parsing decision matrix

| Input | Primary | Gemini fallback | Notes |
|---|---|---|---|
| Digital/native PDF with adequate text | LiteParse text layer | None | No vision cost |
| Scanned PDF with adequate local OCR | LiteParse local OCR | None | No vision cost |
| Specific unreadable pages | LiteParse | Targeted Gemini Vision (only if enabled) | Page numbers and completeness checked |
| Missing LiteParse installation / fatal parser error | LiteParse fails | Full Gemini Vision if enabled and page cap supports *all* pages | Otherwise failure; never claim partial report complete |
| DOCX / XLSX / CSV | Existing non-PDF text/table extractor | No vision in this pipeline | No PDF OCR needed |

## Expected token reduction (not yet measured)

- Prior: 1 financial extraction + 10 Gemini role calls, mostly with whole DPR context.
- Now: 1 financial extraction + 7 agent calls in typical path, with source pages routed by role. Research, Policy and Report roles are represented with deterministic structured outputs pending source-backed integrations.
- A budget for Gemini 2.5 thinking and maximum output tokens is configurable.
- Precise token savings require repeat runs on the same document and model, comparing `dpr_run_token_usage` and `dpr_gemini_call` (including `thinking_tokens`). Do not claim a measured percentage yet.

## Deployment

- Replace your Core LAS code with the changed files using your standard deployment process, or apply the entire cleaned source ZIP; no new database migration is required by these changes.
- Verify the existing `DPR_AGENTIC_ENABLED`, `DPR_DATABASE_URL`, staging key and Gemini secrets.
- `CAM_ENGINE_ENABLED=true` is the new default for new runs unless an environment setting explicitly overrides it.
- Optional tuning: `DPR_AGENTIC_THINKING_BUDGET=512`, `DPR_AGENTIC_MAX_OUTPUT_TOKENS=6144`.
- For scanned PDFs: ensure LiteParse's Node CLI and local OCR dependencies exist on the worker; enable Gemini Vision only if warranted: `DPR_AGENTIC_VISION_ENABLED=true` and set a deliberate page cap.
- Existing completed reports are immutable/versioned. Re-run analysis to see newly structured profiles and finance data in a new CAM version; changing templates will not rewrite an old PDF.
- Do not treat a DPR-sourced borrower name as verified Wallet KYC identity. Approved borrower name from an authenticated Core LAS record should take precedence when available.
- External market and SBI eligibility cannot be independently verified without a real retrieval source. Both remain explicitly unverified; no sanction or subsidy approval is inferred.

## Tests and limitations

- Python compileall succeeded.
- Existing selected financial, CAM rendering, parsing, verification and sector tests passed (see run log).
- Six new offline regression tests passed for borrower/project fact verification, missing name, cost/loan, six-page PDF stress, context routing, page-number fidelity, and subsidy treatment.
- Complete suite cannot run in this execution environment: `respx`, `tenacity` and `langgraph` are not installed. There has been no live Gemini, LiteParse CLI, Wallet, PostgreSQL or full upload-to-report validation in this session.
- The sample/current PDF is a *rendered report*, not the original DPR used for its analysis. Exact financial correctness for the underlying Sushma DPR cannot be certified without that source and the run's structured agent outputs.
- Financial-unit semantics across all arbitrary DPRs remain a validation concern. Configure/test your project-specific units before release and independently reconcile every monetary statement.
