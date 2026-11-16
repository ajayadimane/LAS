-- Apply to Core LAS-owned PostgreSQL only, after migrations 001-010.
--
-- Gemini-vision OCR text per (document sha256, page, OCR model), reused when
-- the same document is analysed again so a re-run reads identical text
-- (app/services/dpr_agentic/ocr_cache.py). Document content: Fernet-encrypted
-- with the staging key, and deleted after DPR_OCR_CACHE_TTL_DAYS by the
-- worker's poll loop.
CREATE TABLE IF NOT EXISTS dpr_ocr_cache (
    document_sha256 TEXT NOT NULL,
    page INTEGER NOT NULL,
    model TEXT NOT NULL,
    ciphertext BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (document_sha256, page, model)
);

CREATE INDEX IF NOT EXISTS dpr_ocr_cache_expires_at ON dpr_ocr_cache (expires_at);
