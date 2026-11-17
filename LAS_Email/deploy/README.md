# Deploying on the local server

Everything runs on one Linux server (Ubuntu 22.04+ assumed), next to Core LAS. Only Gemini (LLM),
the Zoho mailbox (IMAP) and Core LAS (`CORE_INTAKE_URL`) are reached over the network; storage,
document parsing (LiteParse + Tesseract OCR), antivirus, queue and database are local. This service
sends no mail and serves no HTTP: Core LAS does every reply.

## 1. System packages
```bash
sudo apt install postgresql redis-server clamav-daemon libmagic1 tesseract-ocr \
     libreoffice-core libreoffice-writer   # LiteParse converts DOCX through LibreOffice
sudo systemctl enable --now redis-server clamav-daemon
```
Python 3.10 (Ubuntu 22.04's `python3`) is enough. Celery is capped below 5.7 because Ubuntu 22.04
ships Redis 6.0 (see `pyproject.toml`).

## 2. User, code, storage
```bash
sudo useradd -r -m -d /opt/las-email las
sudo mkdir -p /srv/las/objects && sudo chown -R las:las /srv/las && sudo chmod 700 /srv/las/objects
sudo -u las git clone <repo> /opt/las-email && cd /opt/las-email && sudo -u las make install
sudo -u las touch .env && sudo chmod 600 .env
```
In `.env` set at least: `ENV=prod`, `DATABASE_URL`, `LOCAL_STORE_PATH=/srv/las/objects`,
`LOCAL_STORE_KEY_B64` (generate below), `URL_SIGNING_KEY`, `IMAP_PASSWORD`, `GEMINI_API_KEY`,
`CORE_INTAKE_URL` and `CORE_INTAKE_API_KEY` (= Core's `INTAKE_API_KEY`).
```bash
python3 -c 'import os,base64;print(base64.b64encode(os.urandom(32)).decode())'   # LOCAL_STORE_KEY_B64
```

OCR language data (English, Hindi, Kannada) — fetch once so OCR works without internet:
```bash
sudo mkdir -p /srv/las/tessdata && cd /srv/las/tessdata
for l in eng hin kan; do sudo curl -LO https://github.com/tesseract-ocr/tessdata_fast/raw/main/$l.traineddata; done
```
and set `TESSDATA_PATH=/srv/las/tessdata`.

## 3. Database
UAT and prod: **one database shared with Core LAS**, this service in schema `email`. The DBA
creates it once with Core's `deploy/db/setup.sql`; the full runbook is in Core's
`deploy/db/README.md`. In `.env`:
```
DATABASE_URL=postgresql+asyncpg://email_app:...@db-host:5432/las_uat        # rows only
DB_SCHEMA=email
MIGRATION_DATABASE_URL=postgresql+asyncpg://email_owner:...@db-host:5432/las_uat
```
Then, on every deploy (Alembic connects as `email_owner`, the services as `email_app`):
```bash
sudo -u las make db-check migrate seed
```
Add each BD (`bd_partners`: email, `ibkart_bd_code` = Core's BD id); keep the list in step with
Core's `INTAKE_BD_SENDERS`.

**First start on an existing mailbox:** the listener processes every mail in INBOX newer than its
checkpoint (none = all of them). Set the checkpoint to the newest UID first, or mail already in
the inbox is forwarded to Core (and strangers get Core's "not a registered BD" reply).

## 4. Services
```bash
sudo cp deploy/systemd/*.service /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl enable --now las-worker las-beat las-ingest
```
`las-beat` and `las-ingest` must run as exactly one instance each.

## 5. Backups (nothing does this for you)
- Database: nightly `pg_dump -Fc las_<env>` (both services), or `pg_dump -Fc -n email las_<env>`
  for this service only.
- Files: `/srv/las/objects` (already encrypted) — rsync/restic to another machine.
- **`LOCAL_STORE_KEY_B64`**: keep it in a password manager / offline, separately from the file
  backups. Without it the stored mails and attachments cannot be decrypted.

## 6. Monitoring (nothing does this for you either)
- The three systemd units being `active`.
- `core_intake_handoffs`: rows stuck in `PENDING` (Core unreachable or refusing the key) or any
  `REJECTED` (Core refused the event: logged as `core_intake_rejected`).
- **Nightly audit-chain verification** (`las.jobs.audit_verify`, 02:30 IST) logs
  `audit_chain_verification_failed` on a broken chain; alert on that and on the absence of the job.
- Errors go to logs (structlog): ship them somewhere queryable.
- Disk space: `/srv/las/objects` grows continuously (nothing prunes it), PostgreSQL's data
  directory, and ClamAV's virus database.
- Zoho app-password rotation/lockout isn't automated: if IMAP login starts failing, `las-ingest`
  retries and logs, but someone has to rotate the app password in Zoho and update `.env`.

## Updating
```bash
cd /opt/las-email && sudo -u las git pull && sudo -u las make install && sudo -u las make migrate
sudo systemctl restart las-worker las-beat las-ingest
```
