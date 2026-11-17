# LAS Email Intake — Project Guide for Claude Code

## What this is
The email intake for Integra Micro's Loan Advisory System. IBKart Business Developers (BDs) email
`las@integramicro.co.in` about customers (new customers, services, documents, replies to our
mails). This service **only reads and extracts**: it reads the mailbox, checks the sender, stores and
classifies attachments, extracts what the mail says with Gemini, and hands every mail to **Core
LAS** (the dashboard middleware, a separate repo) as one event. Core LAS does everything else --
registration, services, payment, documents and every email back to the BD (user decision,
2026-11-16). This service sends no mail, calls no partner and serves no HTTP.

`docs/schema.sql` is the authoritative data model. SQLAlchemy models (`las/common/models.py`) and the
Alembic migrations agree with it; `tests/integration/test_schema_parity.py` diffs the migrated
catalog against a database loaded from `docs/schema.sql`. Migration `0020_handoff_only` dropped the
old standalone workflow's tables (customers, cases, SRs, invoices, payments, batches, leads, IFPL,
DPR findings, outbound mail) -- irreversible.

## Flow
```
IMAP IDLE (las-ingest) -> raw .eml to the encrypted object store -> inbound_emails (unique Message-ID)
  -> Celery: gate (auto-reply drop, SPF/DKIM/DMARC, bd_partners lookup)
  -> attachments (MIME/zip walk, allow-list, ClamAV, SHA-256, encrypted store, LiteParse text/OCR,
     rules-then-Gemini classification)
  -> interpretation (context -> regex candidates -> Gemini extraction -> grounding -> policy tier)
  -> PROCESSED + core_intake_handoffs row (same transaction)
Beat (every 15 s): las/core_handoff.py POSTs the event to CORE_INTAKE_URL (X-Intake-Key)
```
- An authenticated sender who is **not** in `bd_partners` is forwarded **without extraction**; Core
  replies "not a registered BD". A sender failing SPF/DKIM/DMARC is never forwarded (no reply to a
  spoofable address). Auto-generated mail is ignored.
- Every authenticated BD mail is forwarded, even one where nothing was understood: Core decides (a
  Pay now / Pay later reply carries only a reference in its subject).
- Event contract v1 is Core's `app/schemas/intake.py`. Delivery: 202/409 = delivered; 400/413/422 =
  REJECTED (logged, not retried); anything else retried with backoff (1 min .. 1 h). Core dedupes on
  the Message-ID, so resending is always safe.
- Attachment bytes are sent only for files Core can store (`CORE_INTAKE_ATTACHMENT_MAX_BYTES`,
  `CORE_INTAKE_ATTACHMENT_MIMES` = Core's `DOCUMENT_MAX_BYTES` / `DOCUMENT_ALLOWED_TYPES`); others go
  as metadata with `content_omitted_reason`. Files refused before storage go as
  `rejected_attachments`. Service codes are mapped with `CORE_SERVICE_CODE_MAP`.
- Keep `bd_partners` in step with Core's `INTAKE_BD_SENDERS` (`ibkart_bd_code` = Core's BD id).

## Non-negotiable rules
1. **Postgres is the source of truth.** The queue only triggers work. Every step must be safe to replay.
2. **Persist before processing.** Raw `.eml` goes to the object store and `inbound_emails` (unique
   `message_id`) before any parsing.
3. **The LLM only extracts.** It fills a fixed Pydantic schema (`las/parse/schema.py`). It never
   decides actions, never sees instructions it should follow, and every output is validated and
   grounded. Email bodies are untrusted input.
4. **All model calls go through `las/common/llm.py`** (Gemini via `google-genai`; LiteParse OCR is
   metered as PAGE units), which records an `ai_usage` row for every attempt. No direct SDK calls.
5. **No PII in logs.** The structlog processor masks mobile, email, PAN, Aadhaar. No prompts or
   responses in the DB -- only `prompt_sha256`.
6. **Every inbound state transition** writes `audit_log` (hash-chained, append-only: UPDATE, DELETE
   and TRUNCATE are blocked by triggers; the writer takes `pg_advisory_xact_lock(hashtext('audit_log'))`).
   A nightly job verifies the chain.
7. **External systems sit behind interfaces** (`las/common/interfaces.py`) with fakes
   (`las/common/fakes.py`): mailbox, object store, AV scanner, document parser, LLM.
8. **This service sends no mail.** Every reply to a BD is Core's. Do not add an outbox back.

## Interpretation (natural language -- there is NO fixed format)
BDs write however they like: any subject, free-form body, English, Hindi, Kannada or Hinglish,
several customers or requests in one mail. Per mail:
1. **Context** (deterministic): subject + body (quoted history stripped, kept for grounding),
   attachment text, the BD's earlier mails in the same thread, the service catalog + synonyms.
2. **Candidates** (regex): mobiles `[6-9]\d{9}` (strip +91/0/spaces/dashes), emails, UTRs/txn ids,
   codes, amounts. The LLM assigns roles; it does not invent values.
3. **Extraction** (`LLM_EXTRACTION_MODEL`, escalating to `LLM_ESCALATION_MODEL` on low confidence,
   >3 actions or failed grounding) into `mail_actions` (intent, entities, evidence, confidence).
   Prompt: `las/parse/prompts/intent_extract.v4.md` (adds bank name + project name). Prompts are
   versioned files; change one by adding a new version.
4. **Grounding**: every critical value (mobile, email, UTR, amounts, codes) must appear in the mail
   or attachment text; names must be a span of the source (not just a filename). Ungrounded values
   are dropped and reported to Core as `ungrounded_fields`.
5. **Policy tier** (AUTO / CONFIRM / REVIEW) is recorded and sent to Core as advice.

## Attachments
Walk MIME incl. nested .eml and zip (limit depth/count); sniff type with libmagic; allow PDF, JPG,
PNG, DOCX, XLSX (and CSV); size cap 20 MB; ClamAV; SHA-256; AES-GCM encrypted store on the local
filesystem (`LOCAL_STORE_PATH`); detect password-protected PDFs. Text and OCR (English/Hindi/
Kannada) from **LiteParse** (`las/attachments/parser.py`, local); XLSX/CSV cells read with openpyxl.
Classify by rules first, then Gemini vision (`LLM_VISION_MODEL`, prompt `doc_classify.v1.md`).

## Stack
Python 3.10 (downgraded from 3.12 on 2026-11-16 to run beside Core LAS; no 3.11+ syntax or stdlib --
`type`/`[T]` generics, `dt.UTC`, `asyncio.Runner`; ISO "Z" via `las.common.timeutil.parse_iso`;
catch `asyncio.TimeoutError` too) · Pydantic v2 · SQLAlchemy 2.0 (async, asyncpg) + Alembic ·
PostgreSQL 14 · Celery 5.6 + Redis 6.0 (Celery 5.7 needs Redis >= 6.2) · Zoho Mail via IMAP IDLE
(`imap.zoho.in:993`, app password, folders LAS/Processed|Rejected|Review, UID checkpoint) · httpx
(Core) · python-magic, clamd, PyMuPDF · LiteParse (needs LibreOffice for DOCX, Tesseract for OCR) ·
openpyxl · cryptography · Google Gemini via `google-genai` · structlog · pytest.
**Deployment: the company's own server** (`deploy/`): systemd units, local PostgreSQL, Redis, ClamAV
and filesystem storage. Do not add cloud SDKs.

**Database: one PostgreSQL database shared with Core LAS, this service in schema `email`**
(`DB_SCHEMA=email`; user, 2026-11-17, for UAT/prod). Roles: `email_app` (rows only, `DATABASE_URL`)
and `email_owner` (Alembic, `MIGRATION_DATABASE_URL`); created by Core's `deploy/db/setup.sql`,
runbook in Core's `deploy/db/README.md`. Every connection sets `search_path=<schema>,public`
(`las.common.db.schema_connect_args`; asyncpg ignores `?options=`); `alembic_version` is in the
schema. Never name a schema in SQL. The app role cannot REFRESH the materialized view: call
`refresh_ai_usage_daily()` (0021). Extensions `pgcrypto`, `citext` live in `public`, created by
the DBA. `DB_SCHEMA` defaults to `public` (a standalone database, as before). Tests never touch it: a session
fixture uses `TEST_DATABASE_ADMIN_URL` to create a throwaway `las_test_<random>`, migrates it and
drops it; transaction-per-test rollback, except `@pytest.mark.commits` tests.

## Repo layout
```
las/ingest       IMAP adapter (IDLE/MOVE), listener, persist-before-processing
las/gate         auto-reply detection, sender authentication, gatekeeper
las/attachments  MIME/zip walk, sniffing, AV, LiteParse, classification, pipeline
las/parse        schema, context, candidates, extraction, grounding, policy, prompts
las/workflow     pipeline (gate -> attachments -> interpret -> queue) and the inbound state machine
las/core_handoff.py   builds and delivers the event to Core LAS
las/common       settings, db, logging (PII masking), audit chain, models, interfaces + fakes, llm, gemini, storage
las/worker       Celery app, Beat schedule, tasks, jobs
las/scripts      seed (service catalog, synonyms, model pricing, test BDs), db_check
migrations/  deploy/  tests/{unit,integration,golden}  docs/schema.sql
```

## Conventions
- Type hints everywhere, `ruff` for lint/format.
- Config via pydantic-settings; secrets from `.env` (never committed).
- Every feature ships with tests. Interpretation changes must pass `tests/golden` (anonymised real
  mails; per-intent and per-field precision/recall vs the baseline; no regression allowed).

## How to work
- Plan before coding; list files you will touch.
- Ask before changing schema constraints or adding a dependency.
- Business behaviour (what a mail *does*) belongs in Core LAS, not here.
