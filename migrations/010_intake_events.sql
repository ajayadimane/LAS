-- Apply to Core LAS-owned PostgreSQL only, after migrations 001-009.
--
-- Email intake (user decision, 2026-11-16): LAS_Email, a separate service,
-- reads the mailbox and extracts what a BD's email says, then pushes one
-- event per email to POST /internal/v1/intake/events. These tables persist
-- each event before anything acts on it (app/services/intake/store.py).
--
-- The event body and attachment bytes carry customer PII and documents, so
-- both are Fernet-encrypted (settings.intake_encryption_key). Only what is
-- needed to route and dedupe stays in clear: the email's Message-ID and the
-- BD's own sender address.

CREATE TABLE IF NOT EXISTS intake_events (
    intake_event_id UUID PRIMARY KEY,
    -- The email's Message-ID: LAS_Email retries a push until it gets an
    -- answer, so a repeat must never become a second event.
    event_id TEXT NOT NULL UNIQUE,
    sender_email TEXT NOT NULL,
    bd_code TEXT,
    received_at TIMESTAMPTZ,
    event_ciphertext BYTEA NOT NULL,
    status TEXT NOT NULL DEFAULT 'RECEIVED'
        CHECK (status IN ('RECEIVED', 'PROCESSING', 'PROCESSED', 'IGNORED', 'FAILED')),
    attempts INT NOT NULL DEFAULT 0,
    lease_until TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS intake_events_status_created
    ON intake_events (status, created_at);

CREATE TABLE IF NOT EXISTS intake_attachments (
    intake_event_id UUID NOT NULL
        REFERENCES intake_events(intake_event_id) ON DELETE CASCADE,
    attachment_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes INT NOT NULL CHECK (size_bytes >= 0),
    sha256 CHAR(64) NOT NULL,
    doc_class TEXT,
    -- NULL when LAS_Email did not send the bytes (too large or a type we
    -- cannot store); the metadata is still kept so the BD can be told.
    ciphertext BYTEA,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (intake_event_id, attachment_id)
);
