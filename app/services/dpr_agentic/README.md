# Core LAS Agentic DPR — increment 5A/5B (feature gated)

This is an **initial integration increment**, not a production-complete DPR engine. It accepts **already parsed** text with page references, so Core LAS remains document-ownership-neutral. It DOES NOT move the LAS_Email DPR engine, write Wallet records, or bypass payment gates.

- Disabled by default (`DPR_AGENTIC_ENABLED=false`). Internal API requires `DPR_AGENTIC_INTERNAL_TOKEN` and allows no unauthenticated requests.
- Uses Gemini REST with strict JSON output and sequential specialist roles.
- Source quote page-checking is deterministic; it verifies literal citation alignment **only**, not the financial truth of the claim.
- No current policy retrieval, live research, Python calculator, persistence, asynchronous job processing, payment validation, or report PDF generation is provided yet. Therefore it is NOT deployable to borrowers as a production analysis service.
- Planned follow-on: authenticated Wallet document adapter; durable PostgreSQL-backed job orchestration; independent calculation tool; policy/research retrieval; verified report adapter; role/consent gating; latency/cost budgets and evaluation on the 5 sample DPRs.
