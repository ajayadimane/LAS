-- Apply to Core LAS-owned PostgreSQL only, after migrations 001-015.
--
-- Leads (user, 2026-11-18): an authenticated sender who is not a registered
-- BD gets the lead-capture mail (the old LAS_Email T36) with a link to a
-- details form. A submission is kept here and emailed to the LAS Admin, who
-- routes it to a BD by hand (Core has no BD-by-district table).
--
-- token_id is the nonce inside the signed link: one link, one submission.

CREATE TABLE IF NOT EXISTS intake_leads (
    lead_id BIGSERIAL PRIMARY KEY,
    token_id TEXT NOT NULL UNIQUE,
    sender_email TEXT NOT NULL,
    mobile TEXT NOT NULL,
    state TEXT NOT NULL,
    district TEXT NOT NULL,
    pincode TEXT,
    consent_sha256 TEXT NOT NULL,
    admin_notified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS intake_leads_mobile_idx ON intake_leads (mobile);
