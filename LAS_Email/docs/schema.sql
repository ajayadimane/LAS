-- =====================================================================
-- LAS (Loan Advisory System) — Email Channel schema (read + extract only)
-- PostgreSQL 14+
-- This service reads the mailbox, checks the sender, extracts what a BD's
-- mail says and hands each mail to Core LAS (core_intake_handoffs). Customers,
-- services, payments and replies live in Core LAS (migration 0020 dropped
-- the old workflow's tables, 2026-11-16).
-- Conventions: text + CHECK instead of ENUM (easier to evolve),
-- all timestamps timestamptz (UTC), money numeric(12,2) in INR.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

-- ---------------------------------------------------------------------
-- 1. BD master (synced from IBKart)
-- ---------------------------------------------------------------------
CREATE TABLE bd_partners (
    bd_id              bigserial PRIMARY KEY,
    ibkart_bd_code     text        NOT NULL UNIQUE,
    full_name          text        NOT NULL,
    email              citext      NOT NULL UNIQUE,     -- registered sender address
    mobile             varchar(10),
    region             text,
    status             text        NOT NULL DEFAULT 'ACTIVE'
                       CHECK (status IN ('ACTIVE','SUSPENDED','DEACTIVATED')),
    daily_request_cap  int         NOT NULL DEFAULT 50,  -- abuse throttle
    last_synced_at     timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 4. Service catalog & versioned pricing
-- ---------------------------------------------------------------------
CREATE TABLE service_catalog (
    service_code          text PRIMARY KEY,   -- e.g. DPR_GEN, DPR_ANALYSIS, ID_VERIFY,
                                              -- LAND_VERIFY, SMALL_BORROWING, APPRAISAL
    service_group         smallint    NOT NULL CHECK (service_group IN (1,2,3)),
    display_name          text        NOT NULL,
    menu_order            smallint    NOT NULL,
    requires_re_referral  boolean     NOT NULL DEFAULT false,  -- true for service group 2 (routed to IFPL)
    re_partner_code       text,                                -- 'IFPL' when requires_re_referral
    requires_input_file   boolean     NOT NULL DEFAULT true,   -- false for IFPL-routed services
    CHECK (NOT requires_re_referral OR re_partner_code IS NOT NULL),
    sla_business_hours    int         NOT NULL,
    is_active             boolean     NOT NULL DEFAULT true
);

-- ---------------------------------------------------------------------
-- 6. Inbound mail + attachments
-- ---------------------------------------------------------------------
CREATE TABLE inbound_emails (
    inbound_id         bigserial PRIMARY KEY,
    message_id         text        NOT NULL UNIQUE,     -- idempotency key
    provider_uid       text,
    in_reply_to        text,
    references_hdr     text,
    from_address       citext      NOT NULL,
    from_display       text,
    to_addresses       citext[]    NOT NULL,
    cc_addresses       citext[],
    subject            text,
    body_text          text,
    received_at        timestamptz NOT NULL,
    raw_eml_uri        text        NOT NULL,            -- raw .eml persisted first
    raw_sha256         char(64)    NOT NULL,
    spf_result         text,
    dkim_result        text,
    dmarc_result       text,
    is_auto_generated  boolean     NOT NULL DEFAULT false,  -- Auto-Submitted / OOO
    bd_id              bigint      REFERENCES bd_partners(bd_id),
    sender_verdict     text        CHECK (sender_verdict IN
                         ('VERIFIED','NOT_REGISTERED','SUSPENDED','AUTH_FAILED')),
    -- A natural-language mail can carry several actions; they live in mail_actions.
    -- primary_intent is only a convenience for dashboards.
    primary_intent     text,
    detected_language  text,                         -- e.g. 'en', 'hi', 'kn', 'hi-Latn' (Hinglish)
    parse_method       text        CHECK (parse_method IN ('LLM','LLM_ESCALATED','THREAD_CONTEXT')),
    parse_confidence   numeric(4,3),
    parsed_payload     jsonb,                         -- full validated extraction output
    processing_status  text        NOT NULL DEFAULT 'RECEIVED'
                       CHECK (processing_status IN ('RECEIVED','PARSED','PROCESSED',
                              'REJECTED','NEEDS_REVIEW','FAILED','IGNORED')),
    error_detail       text,
    processed_at       timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_inbound_status  ON inbound_emails(processing_status, received_at);
CREATE INDEX ix_inbound_bd_day  ON inbound_emails(bd_id, received_at);

CREATE TABLE email_attachments (
    attachment_id        bigserial PRIMARY KEY,
    inbound_id           bigint      NOT NULL REFERENCES inbound_emails(inbound_id),
    original_filename    text        NOT NULL,
    declared_mime        text,
    detected_mime        text        NOT NULL,          -- magic-byte sniffed
    size_bytes           bigint      NOT NULL,
    sha256               char(64)    NOT NULL,
    storage_uri          text        NOT NULL,          -- encrypted bucket path
    av_status            text        NOT NULL DEFAULT 'PENDING'
                         CHECK (av_status IN ('PENDING','CLEAN','INFECTED','ERROR')),
    is_password_protected boolean    NOT NULL DEFAULT false,
    page_count           int,
    doc_class            text        CHECK (doc_class IN
                           ('DPR','PAN','AADHAAR','UDYAM','LAND_RECORD','QUOTATION',
                            'BANK_STATEMENT','PAYMENT_PROOF','CONSENT_FORM','BATCH_SHEET',
                            'NA_PERMISSION','BUILDING_PLAN_APPROVAL','POLLUTION_CONSENT','FIRE_NOC',
                            'ELECTRICITY_LOAD_SANCTION','GROUNDWATER_NOC','DAIRY_COOP_TIEUP_LETTER',
                            'ENCUMBRANCE_CERTIFICATE','LEASE_DEED','OTHER')),
    doc_class_confidence numeric(4,3),
    created_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_attach_sha ON email_attachments(sha256);

-- ---------------------------------------------------------------------
-- 6a. Natural-language interpretation
-- Mails are free-form. The LLM turns each mail into a list of actions;
-- deterministic code grounds, validates and executes them.
-- ---------------------------------------------------------------------
CREATE TABLE mail_actions (
    action_id           bigserial PRIMARY KEY,
    inbound_id          bigint      NOT NULL REFERENCES inbound_emails(inbound_id),
    seq                 smallint    NOT NULL,          -- order within the mail
    intent              text        NOT NULL CHECK (intent IN
                          ('NEW_CUSTOMER','MULTI_CUSTOMER','SELECT_SERVICES','SUBMIT_DOCUMENT',
                           'PAYMENT_PROOF','STATUS_QUERY','CONFIRM','CORRECTION',
                           'CLARIFICATION_REPLY','CANCEL_REQUEST','GENERAL_QUESTION','OUT_OF_SCOPE')),
    entities            jsonb       NOT NULL,          -- validated fields for this action
    evidence            jsonb       NOT NULL,          -- {field: verbatim span from mail/attachment}
    grounded            boolean     NOT NULL,          -- every critical value found verbatim in source
    confidence          numeric(4,3) NOT NULL,
    risk_tier           text        NOT NULL CHECK (risk_tier IN ('AUTO','READBACK','CONFIRM','REVIEW')),
    status              text        NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN ('PENDING','EXECUTED','AWAITING_CLARIFICATION',
                                          'AWAITING_CONFIRMATION','NEEDS_REVIEW','REJECTED',
                                          'SUPERSEDED')),
    result_detail       text,
    executed_at         timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (inbound_id, seq)
);
CREATE INDEX ix_mail_actions_status ON mail_actions(status) WHERE status <> 'EXECUTED';

-- Natural-language names BDs use for services, fed to the extraction prompt
CREATE TABLE service_synonyms (
    service_code  text  NOT NULL REFERENCES service_catalog(service_code),
    phrase        text  NOT NULL,                     -- 'project report', 'cibil check', 'kyc'
    language      text  NOT NULL DEFAULT 'en',
    PRIMARY KEY (service_code, phrase, language)
);

-- ---------------------------------------------------------------------
-- 10a. AI usage metering (LLM tokens, OCR pages)
-- Every model call goes through one client wrapper that writes a row here.
-- Prompts/responses are NOT stored inline (PII); only hashes + optional
-- redacted copies in GCS under a retention policy.
-- ---------------------------------------------------------------------
CREATE TABLE ai_model_pricing (
    pricing_id                bigserial PRIMARY KEY,
    provider                  text          NOT NULL,   -- 'google_gemini', 'google_docai'
    model                     text          NOT NULL,   -- e.g. 'gemini-3.5-flash'
    currency                  char(3)       NOT NULL DEFAULT 'USD',
    input_per_mtok            numeric(12,6),            -- price per 1M input tokens
    output_per_mtok           numeric(12,6),
    cache_write_per_mtok      numeric(12,6),
    cache_read_per_mtok       numeric(12,6),
    per_unit_price            numeric(12,6),            -- non-token services (OCR per page)
    usage_unit                text          NOT NULL DEFAULT 'TOKEN'
                              CHECK (usage_unit IN ('TOKEN','PAGE','REQUEST')),
    batch_discount_pct        numeric(5,2)  NOT NULL DEFAULT 0,
    valid_from                date          NOT NULL,
    valid_to                  date,
    UNIQUE (provider, model, valid_from)
);

CREATE TABLE ai_usage (
    usage_id                  bigserial PRIMARY KEY,
    occurred_at               timestamptz   NOT NULL DEFAULT now(),
    provider                  text          NOT NULL,
    model                     text          NOT NULL,
    pricing_id                bigint        REFERENCES ai_model_pricing(pricing_id),
    purpose                   text          NOT NULL CHECK (purpose IN
                                ('INTENT_EXTRACTION','BATCH_ROW_EXTRACTION','DOC_CLASSIFICATION',
                                 'OCR','DPR_GENERATION','DPR_ANALYSIS','APPRAISAL_NARRATIVE',
                                 'QUOTATION_CHECK','OTHER')),
    prompt_template           text          NOT NULL,   -- e.g. 'intent_extract'
    prompt_version            text          NOT NULL,   -- e.g. 'v3'
    prompt_sha256             char(64),
    provider_request_id       text,                     -- provider request/response id, for support/dispute
    attempt_no                smallint      NOT NULL DEFAULT 1,   -- retries are separate rows
    is_batch_api              boolean       NOT NULL DEFAULT false,
    -- token counts as returned by the API usage block
    input_tokens              int           NOT NULL DEFAULT 0,
    output_tokens             int           NOT NULL DEFAULT 0,
    cache_creation_tokens     int           NOT NULL DEFAULT 0,
    cache_read_tokens         int           NOT NULL DEFAULT 0,
    units                     int           NOT NULL DEFAULT 0,   -- OCR pages etc.
    cost                      numeric(12,6),                      -- computed at write time from pricing
    currency                  char(3)       NOT NULL DEFAULT 'USD',
    latency_ms                int,
    status                    text          NOT NULL CHECK (status IN
                                ('SUCCESS','VALIDATION_FAILED','API_ERROR','TIMEOUT','BUDGET_BLOCKED')),
    stop_reason               text,
    error_detail              text,
    -- attribution: set whatever is known at call time
    bd_id                     bigint        REFERENCES bd_partners(bd_id),
    inbound_id                bigint        REFERENCES inbound_emails(inbound_id),
    attachment_id             bigint        REFERENCES email_attachments(attachment_id),
    CHECK (input_tokens >= 0 AND output_tokens >= 0
           AND cache_creation_tokens >= 0 AND cache_read_tokens >= 0)
);
CREATE INDEX ix_ai_usage_time    ON ai_usage(occurred_at);
CREATE INDEX ix_ai_usage_inbound ON ai_usage(inbound_id) WHERE inbound_id IS NOT NULL;
CREATE INDEX ix_ai_usage_bd_time ON ai_usage(bd_id, occurred_at);

-- Daily rollup for dashboards / monthly cost review.
-- bd_key = COALESCE(bd_id, 0) is a plain output column so the unique index
-- below contains no expressions: REFRESH MATERIALIZED VIEW CONCURRENTLY
-- requires a unique index on plain columns only.
CREATE MATERIALIZED VIEW mv_ai_usage_daily AS
SELECT date_trunc('day', occurred_at AT TIME ZONE 'Asia/Kolkata')::date AS day_ist,
       provider, model, purpose, prompt_template, prompt_version, bd_id,
       COALESCE(bd_id, 0)           AS bd_key,
       COUNT(*)                     AS calls,
       SUM(input_tokens)            AS input_tokens,
       SUM(output_tokens)           AS output_tokens,
       SUM(cache_read_tokens)       AS cache_read_tokens,
       SUM(cache_creation_tokens)   AS cache_creation_tokens,
       SUM(units)                   AS units,
       SUM(cost)                    AS cost_usd,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms
FROM ai_usage
GROUP BY 1,2,3,4,5,6,7;
CREATE UNIQUE INDEX uq_mv_ai_usage_daily
    ON mv_ai_usage_daily(day_ist, provider, model, purpose, prompt_template, prompt_version,
                         bd_key);

-- ---------------------------------------------------------------------
-- 11. Append-only, hash-chained audit log
-- ---------------------------------------------------------------------
CREATE TABLE audit_log (
    audit_id     bigserial PRIMARY KEY,
    occurred_at  timestamptz NOT NULL DEFAULT now(),
    actor_type   text        NOT NULL,
    actor_ref    text,
    action       text        NOT NULL,
    entity_type  text        NOT NULL,
    entity_id    text        NOT NULL,
    before_state jsonb,
    after_state  jsonb,
    prev_hash    char(64),
    row_hash     char(64)    NOT NULL
);

CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_no_update BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
-- Row triggers don't fire on TRUNCATE; block it with a statement trigger.
CREATE TRIGGER trg_audit_no_truncate BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_immutable();

-- ---------------------------------------------------------------------
-- 12. Mailbox ingestion checkpoint (IMAP UID per folder + UIDVALIDITY)
-- Only a hint for catch-up: inbound_emails.message_id stays the idempotency key,
-- so losing or resetting a checkpoint causes re-reads, never double processing.
-- ---------------------------------------------------------------------
CREATE TABLE mailbox_checkpoints (
    mailbox        citext      NOT NULL,
    folder         text        NOT NULL,
    uidvalidity    bigint      NOT NULL,
    last_uid       bigint      NOT NULL DEFAULT 0 CHECK (last_uid >= 0),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (mailbox, folder)
);

-- One row per processed mail, pushed to Core LAS as an intake event
-- (las/core_handoff.py). Core LAS owns everything that follows.
CREATE TABLE core_intake_handoffs (
      handoff_id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
      inbound_id BIGINT NOT NULL UNIQUE REFERENCES inbound_emails(inbound_id),
      status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING','IN_FLIGHT','DELIVERED','REJECTED')),
      attempts INTEGER NOT NULL DEFAULT 0,
      next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      lease_until TIMESTAMPTZ,
      last_status_code INTEGER,
      last_error TEXT,
      core_intake_event_id TEXT,
      delivered_at TIMESTAMPTZ,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
CREATE INDEX ix_core_intake_due
  ON core_intake_handoffs(next_attempt_at)
      WHERE status IN ('PENDING','IN_FLIGHT');
