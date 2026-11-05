# Source and integration contract

Classification logic and text-extraction functions adapted from `LAS_Email/las/attachments/classify.py` and `text.py`, without the LAS_Email database, LLM metering or storage dependencies. Keep these shared rules synchronized or publish a proper internal library later.

1. Core LAS authorizes the customer and confirms payment/consent eligibility.
2. Core LAS uploads to Wallet through its existing tested integration.
3. ONLY after Wallet returns an authoritative document ID, invoke `/internal/dpr-agentic/inspect-upload` with original verified bytes and references.
4. This adapter classifies and parses, starts Gemini orchestration only for confidently classified text DPRs.
5. For scanned / sparse PDFs, the adapter returns `needs_vision_fallback`; connect the existing Gemini vision/OCR pipeline in a later increment.
6. There is no durable queue or automatic upload hook yet; do not call this route directly from public browser clients.

**Critical source limitation:** current Core LAS `EnrolmentService.upload_document()` stores base64 content in a Wallet person custom field and returns no Wallet document ID. Therefore a safe upload-trigger hook cannot be wired until the authoritative Wallet document API is identified. This endpoint intentionally does not fabricate IDs or bypass the upload authorization gate.
