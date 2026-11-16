# LAS Email Intake

Integra Micro's Loan Advisory System email intake: IBKart BDs write to `las@integramicro.co.in` in
their own words (English, Hindi, Kannada, Hinglish). This service reads the mail, checks the sender,
stores and classifies the attachments, extracts what the mail says, and hands every mail to
**Core LAS** as one event. Core LAS does everything else, including every reply. See `CLAUDE.md`
for the rules and `docs/schema.sql` for the data model.

## Quick start (local)

```bash
make install                     # Python 3.10 venv (.venv) + pinned deps (requirements.lock)
# .env: DATABASE_URL (…/LAS_Email), IMAP_PASSWORD, GEMINI_API_KEY, CORE_INTAKE_URL,
#       CORE_INTAKE_API_KEY (= Core's INTAKE_API_KEY); locally AV_SCANNER=fake / DOCUMENT_PARSER=fake
#       if ClamAV / Tesseract are not installed
make db-check                    # server version + pgcrypto/citext
make migrate && make seed        # schema + service catalog, synonyms, model pricing, 2 test BDs
TEST_DATABASE_ADMIN_URL=postgresql://… make test   # creates and drops las_test_<random>
make run                         # worker + Beat + mailbox listener in one terminal (Ctrl+C stops)
                                 # needs Redis on localhost:6379 and Core LAS running
```
Before the first `make ingest` on a mailbox that already has mail, set the listener's checkpoint to
the newest UID (see `deploy/README.md`), or every mail in INBOX is processed.

Production on the company server: `deploy/README.md`.

## How a mail flows

```
IMAP IDLE (las-ingest) ─► raw .eml → object store ─► inbound_emails (unique Message-ID) ─► Celery
  └► gate: auto-reply? · Authentication-Results (trusted MX only; DKIM/SPF fallback) · BD lookup
  └► attachments: MIME/zip walk · libmagic allow-list · 20 MB · ClamAV · SHA-256 · encrypted store
                  → LiteParse text / OCR (local, metered) · rules-then-Gemini classification
  └► interpret: context → regex candidates → Gemini structured extraction (escalates on low
                confidence / >3 actions / ungrounded) → grounding → policy tier
  └► PROCESSED + core_intake_handoffs row
Beat ─► las/core_handoff.py ─► POST CORE_INTAKE_URL (X-Intake-Key): 202/409 delivered,
        400/413/422 rejected, anything else retried with backoff
```

## Layout

| path | what |
|---|---|
| `las/ingest` | Zoho IMAP adapter (IDLE/MOVE), listener, persist-before-processing |
| `las/gate` | auto-reply detection, sender authentication, gatekeeper |
| `las/attachments` | MIME/zip walk, sniffing, AV, LiteParse parser (text + OCR), classification, pipeline |
| `las/parse` | schema, context, candidates, extraction, grounding, policy, versioned prompts |
| `las/workflow` | the per-mail pipeline and the inbound state machine |
| `las/core_handoff.py` | builds the v1 event and delivers it to Core LAS |
| `las/common` | settings, db, logging (PII masking), audit chain, models, interfaces + fakes, `llm.py` (the only model entry point), Gemini adapter, encrypted local storage |
| `las/worker` | Celery app, Beat schedule, tasks, jobs |
| `tests/golden` | 45 golden mails; per-intent / per-field precision-recall report + baseline |

## Golden set

`make test-golden` runs in **replay** mode: each case carries the model output (some deliberately
wrong) and the report measures grounding and policy. `make test-golden-live` (with
`GEMINI_API_KEY`) sends the same mails to Gemini and measures the model. Reports:
`tests/golden/report.<mode>.md`; baselines `tests/golden/baseline.<mode>.json` (no regression
allowed).
