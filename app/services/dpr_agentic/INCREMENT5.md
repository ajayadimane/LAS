# Increment 5: actual Core LAS upload wiring (opt-in)

## Implemented

- Customer-owned route: `PUT /api/v1/customer/services/{code}/document-with-analysis?filename=x.pdf` (plus configured context path).
- Authenticates using the existing Core LAS customer dependency and checks existing service eligibility before making either outbound call.
- Reads and validates uploaded bytes using existing Core LAS upload validation.
- Runs the existing Wallet custom-field persistence **and** document classification/parsing + LangGraph analysis concurrently, from immutable request bytes.
- Separately returns Wallet status, AI status, inspection summary, error types, and `NOT_PUBLISHED` report status.
- The existing document upload route is unchanged. `DPR_AGENTIC_ENABLED=false` disables the new endpoint.

## Known production blockers — do not enable as a replacement for current upload yet

1. This request-scoped route is not restart-safe. If the request or process is killed mid-flight, neither branch has durable document payload state or an independent worker claim. The SQL job registry in Increment 3 is not yet connected to an encrypted, recoverable input store.
2. Reports/findings are deliberately not returned or published; the current Core LAS package lacks a verified report artifact store and report-download authorization path. The earlier release-policy helper is not an integration into a live endpoint.
3. The current Wallet integration stores document payload in a person custom field rather than returning a dedicated Wallet document ID. This route uses the actual custom-field source identity; it does not fabricate a Wallet document ID.
4. Scanned DPRs require the Gemini vision fallback; the current code only signals `NEEDS_VISION_FALLBACK`.
5. Existing `EnrolmentService.upload_document` requires `Paid`; the requested Pay Later unpaid-but-consented path requires an authoritative policy change before enabling it.
6. LangGraph/Postgres integration and live Gemini, Wallet, and end-to-end verification remain untested in this environment.
7. Model input is not a safe substitute for third-party policy evidence, and generated calculations still require deterministic verification.

## Next blocking engineering work

Implement an encrypted durable upload staging store with per-branch leases, idempotent wallet reconciliation, worker checkpoints and a status endpoint; connect the Wallet payment authority/consent contract to report read authorization, implement original-document visual extraction, add a secure versioned report artifact store, and run the complete test suite with deployment dependencies.
