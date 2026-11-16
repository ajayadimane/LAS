-- Apply to Core LAS-owned PostgreSQL only, after migrations 001-010.
--
-- Email intake cases (app/services/intake/cases.py): one per customer a BD
-- emails us about, from the first email until the customer is registered
-- and paid for. The customer's details are Fernet-encrypted
-- (settings.intake_encryption_key); only routing columns stay in clear.

CREATE TABLE IF NOT EXISTS intake_cases (
    case_id UUID PRIMARY KEY,
    -- Shown to the BD as [LAS-IN-<ref>] in every subject; replies match on it.
    ref TEXT NOT NULL UNIQUE,
    origin_event_id UUID NOT NULL REFERENCES intake_events(intake_event_id),
    -- Which customer of that email (one email can name several).
    customer_index INT NOT NULL,
    bd_id TEXT NOT NULL,
    bd_email TEXT NOT NULL,
    person_id TEXT,
    state TEXT NOT NULL CHECK (state IN (
        'NEW', 'NEEDS_INFO', 'REGISTERING', 'AWAITING_PAYMENT', 'PAYING',
        'UPLOADING', 'DONE', 'FAILED', 'NEEDS_ATTENTION', 'EXPIRED')),
    data_ciphertext BYTEA NOT NULL,
    reminders_sent INT NOT NULL DEFAULT 0,
    expires_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Reprocessing the same email never opens a second case.
    UNIQUE (origin_event_id, customer_index)
);

CREATE INDEX IF NOT EXISTS intake_cases_state_expires
    ON intake_cases (state, expires_at);

-- Every Message-ID we sent about a case, so a reply (In-Reply-To /
-- References) finds its case even if the BD edits the subject.
CREATE TABLE IF NOT EXISTS intake_case_messages (
    message_id TEXT PRIMARY KEY,
    case_id UUID NOT NULL REFERENCES intake_cases(case_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
