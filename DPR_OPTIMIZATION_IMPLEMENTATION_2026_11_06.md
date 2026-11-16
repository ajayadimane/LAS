# Core LAS DPR optimization and canonical CAM — implementation record

Baseline: user-uploaded `LAS(3).zip`, 6 November 2026. This is an additive change to the existing Core LAS application. No Wallet, LAS_Email, React or payment-authorization code was changed.

## Root causes verified in code

1. `context_router.py`: keyword count ranked entire pages (13 Finance, 10 Sector), causing repeated prompt context and potentially missing dispersed financial schedules.
2. `gemini.py`: numeric `thinkingBudget` was applied only for `gemini-2.5*`, so it did not limit `gemini-3.8-flash`; per-call usage already demonstrated 77,295 tokens, including 14,231 thinking tokens in one prior real run.
3. `gemini.py` + `financial_schema.py`: schema allowed `required: []`; semantic extraction completeness wasn't validated before the financial calculation / CAM pipeline.
4. `cam_adapter.py`: generic repayment relied on YearFinancials iteration even if a full repayment table was extracted, and Year-1 DSCR could be mislabeled as the mean.
5. `cam_render.py`: the KYC card was hard-coded Not yet checked; funding/cost/repayment statuses relied on narrow finding categories and computed traces rather than acknowledging actual flagged discrepancies.
6. `report_store.py` + `report_http.py`: new PDF and structured endpoints reconstructed the CAM snapshot independently; an authoritative, immutable snapshot was never stored alongside the report version.
7. `document_pipeline/pipeline.py`: the JPEG/PNG branch incorrectly attempted `pymupdf.open(...filetype="pdf")` on image bytes when handling weak-page results.

## Implemented changes

- Section-aware financial evidence selection and role-specific contexts (`context_router.py`). Preserves original page numbers and input bytes; no hardcoded section page positions; retains adjacent pages of potential multi-page financial tables. The verifier is given original page content for referenced findings, not only another model's narrative.
- Compact, bounded upstream agent findings passed to downstream roles. Complete agent outputs remain in `AnalysisResult`.
- First financial extraction uses relevant schedule pages. When an extract is incomplete *and the document has the corresponding source sections*, at most one targeted recovery model call runs; observed values are never overwritten by a recovery value.
- A semantic financial completeness predicate distinguishes no usable financial facts from partially extracted data. Empty financial output produces `FINANCIAL_EXTRACTION_INCOMPLETE`, with an `INSUFFICIENT_INFORMATION` CAM readiness state. Partially incomplete sections are explicitly flagged. Values are never copied from narrative findings into authoritative numbers.
- Generic non-harvester loan amount fallback from source-marked `term_loan_amount`, stated means-of-finance total, and repayment schedule from all years present in either the year projections or the loan schedule. Never conflates Year-1 DSCR and average DSCR.
- Summary "What we checked" checks reflect recognized issue categories; explicit identity conflicts require KYC verification, without claiming independent KYC was performed.
- Gemini 3 family uses supported `thinkingLevel`: LOW for OCR/document/planner/correction, MEDIUM for financial extraction/finance/sector/risk/verifier. `DPR_AGENTIC_THINKING_LEVEL=low|medium|high` overrides all Gemini 3 roles; `DPR_AGENTIC_THINKING_BUDGET` still applies to Gemini 2.5. Logs include finish reason, cache tokens, input bytes and call duration. Non-STOP finish reasons are rejected as incomplete.
- Migration `010_canonical_cam_snapshot.sql` adds immutable `cam_snapshot`, `cam_schema_version`, `cam_snapshot_sha256` to `dpr_report_artifacts`, without touching older versions. `store_report()` constructs each new report-version snapshot once, stores it with a normalized SHA-256, and renders its exact JSON through the existing PDF renderer. PDF re-render uses that stored snapshot; the summary and annexures load that same snapshot. For historical pre-010 versions only, the existing dynamic builder fallback remains.
- Report concurrency includes a version-change retry, recomputing only deterministic snapshot/PDF, never re-calling Gemini.
- JPEG/PNG processing no longer treats an image as a PDF.

## Files modified

- `app/core/config.py`
- `app/services/dpr_agentic/context_router.py`
- `app/services/dpr_agentic/orchestrator.py`
- `app/services/dpr_agentic/gemini.py`
- `app/services/dpr_agentic/financial_schema.py`
- `app/services/dpr_agentic/cam_adapter.py`
- `app/services/dpr_agentic/cam_render.py`
- `app/services/dpr_agentic/report_store.py`
- `app/services/dpr_agentic/report_http.py`
- `app/services/dpr_agentic/document_pipeline/pipeline.py`
- `scripts/dpr_worker.py`

## New files

- `migrations/010_canonical_cam_snapshot.sql`
- `scripts/smoke_test_canonical_cam.py`
- `tests/test_dpr_optimization_snapshot_regression.py`
- `tests/test_dpr_gemini_cost_controls.py`
- `DPR_OPTIMIZATION_IMPLEMENTATION_2026_11_06.md`

## Deployment sequence

1. Back up the existing application files and PostgreSQL database. Stop the DPR background worker and do not allow new report writers during migration.
2. Apply PostgreSQL migrations **001–009 first if not already applied**, then `psql "$DPR_DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/010_canonical_cam_snapshot.sql`. Ensure the environment variable points to **Core LAS's** PostgreSQL, not Wallet or LAS_Email. The SQL is additive and idempotent.
3. Deploy the updated Python files and install the existing locked project dependencies in the same interpreter as FastAPI and the spawned PDF renderer.
4. Set `CAM_ENGINE_ENABLED=true`; keep the existing Gemini model configuration. Optional for Gemini 3.8: `DPR_AGENTIC_THINKING_LEVEL=medium` to force a uniform level, or leave unset for LOW/MEDIUM per-role defaults. Never set `minimal`. The legacy numeric budget is still read for Gemini 2.5 models.
5. Run `python scripts/smoke_test_cam_renderer.py` and `python scripts/smoke_test_canonical_cam.py` **before** enabling the worker.
6. Start Core LAS API + DPR worker and submit a single controlled staging DPR; check worker logs for extraction completeness and Gemini usage.
7. Verify that the newly created `dpr_report_artifacts` row has non-null `cam_snapshot`, 64-character `cam_snapshot_sha256`, `pdf_render_status='RENDERED'` and valid PDF bytes. Compare PDF and `/report/summary` values for the **same version**.
8. Measure input, output, thinking tokens and elapsed time for the same reference DPR against the previous 77,295-token baseline. These savings are not measurable offline.

## Rollback

The migration adds only nullable columns and a trigger. To roll back the application, stop workers, deploy the previous code and leave the new columns in place; old code ignores them. **Do not drop the new columns or overwrite versioned reports**, because new records may already rely on the immutable snapshot. The previously established PDF re-render transition and report access gates remain unchanged.

## Offline verification conducted

- 224 existing and newly added regression tests passed in one complete selected offline run before two additional checksum tests were added.
- 14 focused new tests passed after the checksum tests were added.
- Existing six-page PDF rendering regression passed with a 55-finding synthetic case.
- Subprocess smoke tests produced valid PDFs, including one built from a canonical serialized CAM snapshot, with no Gemini call.
- Python compileall passed for modified application and worker source.
- Full repository test collection is blocked by missing `respx` in the available host runtime. The uploaded `.venv` interpreter symlink points to a non-existent local Python path; do not rely on it to validate deployment.

## Known limitations / unverified acceptance criteria

- The new routing and per-agent thinking levels are implemented but **real token savings are unmeasured**. 30k–40k tokens is a stretch benchmark, not a promise.
- There is no local live Gemini, PostgreSQL, Wallet or service-account connection. The migration and real report-write/read authorization flow must be staged and tested before production.
- This does not add authoritative policy/market retrieval or certify SBI/AIF eligibility.
- Legacy completed report versions are deliberately immutable and not backfilled. Repairing an older PDF can reuse its saved analysis, but **populating previously unextracted financial facts requires re-extraction/an authorized new analysis run**, not PDF re-rendering.
- Financial tables still rely on Gemini extraction in some layouts; if values cannot be recovered from the source, the CAM now explicitly reports incomplete evidence rather than inventing numbers. Loan amortization models outside the DPR's own schedule require additional methods and validation.
- There is no claim of a measured lower token count or 100% financial extraction coverage.
