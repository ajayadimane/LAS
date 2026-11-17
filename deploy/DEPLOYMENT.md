# LAS: demo deployment

Everything runs on one demo server, behind its existing nginx:

```
Browser ─► http://<demo-host>/las/      ─► nginx: static UI from /var/www/las/
        └► http://<demo-host>/las-api/  ─► nginx ─► las-backend (container, 127.0.0.1:9000)
                                                     ├► iWallet vendor (https://10.10.20.153:8080)
                                                     ├► Postgres (127.0.0.1:5432, schema core)
                                                     └► Zoho SMTP (all mail to BDs/customers)
las-dpr-worker (container, same image) ─► Postgres + Gemini   (DPR analysis, PDF reports)

Zoho mailbox (IMAP) ─► LAS_Email (systemd: las-ingest, las-worker, las-beat; Redis, ClamAV)
                         ├► Postgres (schema email) + Gemini
                         └► http://127.0.0.1:9000/las-api/internal/v1/intake/events  (never via nginx)
```

| Part | What it is | Where it goes |
|---|---|---|
| Frontend | Static files, already built (`frontend/dist/`). No Node.js needed. | `/var/www/las/` |
| Backend + DPR worker | One Docker image, two containers | Code in `/opt/las/`, settings `/opt/las/.env` |
| Email intake | LAS_Email, Python venv + systemd | `/opt/las-email/`, settings `/opt/las-email/.env` |
| Database | One Postgres database `las_demo`: schema `core` (backend), `email` (LAS_Email) | Local Postgres |

The paths `/las` and `/las-api` are fixed into the frontend build. Don't edit `dist/`.

> **Only one LAS_Email may read the mailbox.** Before starting it here (step 8), stop every other
> copy (the developer's machine included). Two readers process the same mail twice and send the
> BDs every reply twice.

---

## 0. Before you start (one-time checks)

```bash
docker --version          # Docker installed
nginx -v                  # nginx installed
psql --version            # PostgreSQL 14+ (else: sudo apt install postgresql)
python3 --version         # 3.10+ (for LAS_Email)
sudo ss -ltnp | grep -E ':9000|:5432'   # 9000 must be free; 5432 = Postgres listening
```

The server must reach the vendor (office network), Google Gemini and Zoho:

```bash
curl -k -s -o /dev/null -w "%{http_code}\n" https://10.10.20.153:8080            # 404 = good
curl -s -o /dev/null -w "%{http_code}\n" https://generativelanguage.googleapis.com # 404 = good
timeout 5 bash -c '</dev/tcp/imap.zoho.in/993' && echo imap-ok
timeout 5 bash -c '</dev/tcp/smtp.zoho.in/465' && echo smtp-ok
```

A timeout on any of these: fix the network first.

---

## 1. What you receive

From the dev team:

1. **`las-deploy-<commit>.tar.gz`**, made with `scripts/package_demo.sh`. It holds the code
   (backend, `migrations/`, `deploy/`, `LAS_Email/`) and the built UI (`frontend/dist/`).
2. **Secrets, sent separately and privately:** vendor admin and intake BD logins, the Zoho app
   password, the Gemini API key. Never commit them, put them in an image, or paste them in chat.

You generate the rest yourself below (database passwords, encryption keys, the intake key).
Keep a copy of every value in the team's password manager: **the encryption keys cannot be
recovered**, and without them the stored data is unreadable.

---

## 2. Unpack

```bash
sudo mkdir -p /opt/las
sudo tar -xzf las-deploy-*.tar.gz -C /opt/las --strip-components=1
cat /opt/las/REVISION      # the commit you are deploying
```

---

## 3. Database (once)

Generate four passwords, one per role, and keep them:

```bash
for r in core_owner core_app email_owner email_app; do echo "$r $(openssl rand -hex 24)"; done
```

Create the database, roles and schemas (`deploy/db/README.md` explains the layout):

```bash
sudo -u postgres createdb las_demo
sudo -u postgres psql -d las_demo -v ON_ERROR_STOP=1 \
  -v core_owner_password=<..> -v core_app_password=<..> \
  -v email_owner_password=<..> -v email_app_password=<..> \
  -f /opt/las/deploy/db/setup.sql
```

The services connect over TCP to `127.0.0.1:5432` with these passwords (Ubuntu's default
`pg_hba.conf` allows that).

---

## 4. Frontend: copy the built UI

```bash
sudo mkdir -p /var/www/las
sudo cp -r /opt/las/frontend/dist/. /var/www/las/
sudo chown -R www-data:www-data /var/www/las     # nginx's user; `nginx` on RHEL/CentOS
```

---

## 5. Backend settings: `/opt/las/.env`

Generate the keys:

```bash
python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"  # run twice: DPR_STAGING_ENCRYPTION_KEY, INTAKE_ENCRYPTION_KEY
openssl rand -hex 32     # run twice: INTAKE_API_KEY, OTP_TOKEN_SECRET
```

```
ENVIRONMENT=demo
LOG_LEVEL=INFO
HOST=127.0.0.1
PORT=9000
CONTEXT_PATH=/las-api
DASHBOARD_SIGN_IN_URL=https://<demo-host>/las/signin

# Vendor. The vendor's certificate is for *.workflo.ai, not the IP, so TLS
# cannot be verified until they give us a hostname. Demo only.
VENDOR_BASE_URL=https://10.10.20.153:8080
VENDOR_VERIFY_SSL=false
VENDOR_ADMIN_USER_ID=<from dev team>
VENDOR_ADMIN_PASSWORD=<from dev team>

# Mail (customer logins, every intake reply). Zoho app password.
SMTP_PASSWORD=<from dev team>

# OTP: the OTP service's host is not known yet, so registration skips it.
OTP_REQUIRED=false
OTP_TOKEN_SECRET=<generated>

DOCUMENT_MAX_BYTES=5242880

# Database (step 3)
DPR_DATABASE_URL=postgresql+psycopg://core_app:<core_app password>@127.0.0.1:5432/las_demo
DB_SCHEMA=core
DB_MIGRATE_ON_STARTUP=false
DB_MIGRATION_URL=postgresql+psycopg://core_owner:<core_owner password>@127.0.0.1:5432/las_demo

# DPR analysis (the worker container)
DPR_AGENTIC_ENABLED=true
DPR_AGENTIC_GEMINI_API_KEY=<from dev team>
DPR_AGENTIC_GEMINI_MODEL=gemini-3.8-flash
DPR_AGENTIC_VISION_ENABLED=true
DPR_AGENTIC_VISION_MAX_PAGES=100
DPR_STAGING_ENCRYPTION_KEY=<generated>
DPR_WORKER_AUTOSTART=false

# Email intake
INTAKE_ENABLED=true
INTAKE_API_KEY=<generated; LAS_Email's CORE_INTAKE_API_KEY is the same value>
INTAKE_ENCRYPTION_KEY=<generated>
INTAKE_BD_SENDERS={"<bd email>":"<bd id>"}
INTAKE_VENDOR_USER_ID=<from dev team: a BD account>
INTAKE_VENDOR_PASSWORD=<from dev team>
```

```bash
sudo chmod 600 /opt/las/.env
```

Everything else has a working default; `/opt/las/.env.example` lists them all.

**Placeholders the demo runs with** (fine for a demo, not for production): the Pay now UPI id
(`lasservices@upi`) and the settlement account/IFSC sent for every customer. **Pay later moves real
sandbox money** from the pool account.

---

## 6. Backend: build, migrate, start

```bash
cd /opt/las
sudo docker build -t las-backend:$(date +%Y%m%d) -t las-backend:latest .

# Create/upgrade the tables (as core_owner). Every deploy; safe to repeat.
sudo docker run --rm --network host --env-file /opt/las/.env las-backend:latest python scripts/migrate.py

# The API
sudo docker run -d --name las-backend --restart unless-stopped --network host \
  --env-file /opt/las/.env las-backend:latest

# The DPR worker (same image, no HTTP)
sudo docker run -d --name las-dpr-worker --restart unless-stopped --network host --no-healthcheck \
  --env-file /opt/las/.env las-backend:latest python scripts/dpr_worker.py
```

- `--network host` lets the containers reach Postgres at `127.0.0.1`. `HOST=127.0.0.1` keeps the
  API on loopback: nginx is the one public entry point. **Never set `HOST=0.0.0.0` here.**
- `--restart unless-stopped` brings both back after a crash or reboot.
- The date tag keeps the previous image for rollback (step 11).

Check:

```bash
sudo docker ps                                     # las-backend (healthy) after ~30 s, las-dpr-worker Up
curl http://127.0.0.1:9000/las-api/health          # {"status":"ok",...,"environment":"demo"}
sudo docker logs las-backend 2>&1 | grep -i -E "error|schema|intake" | tail
sudo docker logs las-dpr-worker 2>&1 | tail        # no "missing settings" error
```

---

## 7. nginx

Inside the demo host's `server { ... }` block:

```nginx
include /opt/las/deploy/nginx-las.conf;
```

```bash
sudo nginx -t && sudo systemctl reload nginx
curl -s -o /dev/null -w "%{http_code}\n" http://<demo-host>/las-api/internal/v1/intake/events   # 404: blocked, as intended
```

`nginx-las.conf` assumes `/var/www/las/` and port `9000`.

---

## 8. Email intake (LAS_Email)

LAS_Email's own runbook is `/opt/las/LAS_Email/deploy/README.md`. Follow its steps 1, 2 and 4
with these demo specifics. Its step 3 (database) is replaced by what is below.

**Code:** instead of `git clone`, copy it out of the tarball:

```bash
sudo useradd -r -m -d /opt/las-email las 2>/dev/null || true
sudo cp -r /opt/las/LAS_Email/. /opt/las-email/ && sudo chown -R las:las /opt/las-email
```

**`/opt/las-email/.env`**, in addition to what its README lists:

```
DATABASE_URL=postgresql+asyncpg://email_app:<email_app password>@127.0.0.1:5432/las_demo
DB_SCHEMA=email
MIGRATION_DATABASE_URL=postgresql+asyncpg://email_owner:<email_owner password>@127.0.0.1:5432/las_demo
CORE_INTAKE_URL=http://127.0.0.1:9000/las-api/internal/v1/intake/events
CORE_INTAKE_API_KEY=<the backend's INTAKE_API_KEY>
```

**Tables, then reference data without the test BDs:**

```bash
cd /opt/las-email
sudo -u las make db-check migrate
sudo -u las .venv/bin/las-seed --no-test-bds
```

**The BDs** (the same list as the backend's `INTAKE_BD_SENDERS`; `ibkart_bd_code` = the BD id):

```bash
psql "postgresql://email_app:<email_app password>@127.0.0.1:5432/las_demo" -c \
 "INSERT INTO bd_partners (ibkart_bd_code, full_name, email, mobile) VALUES ('<bd id>', '<name>', '<bd email>', '<mobile>');"
```

**The mailbox checkpoint**, so mail already handled on the developer's machine is not handled again.
On the developer's machine, **after stopping its LAS_Email**:

```bash
psql <dev database> -c "SELECT mailbox, folder, uidvalidity, last_uid FROM email.mailbox_checkpoints;"
```

On the demo server, insert the same values:

```bash
psql "postgresql://email_app:<email_app password>@127.0.0.1:5432/las_demo" -c \
 "INSERT INTO mailbox_checkpoints (mailbox, folder, uidvalidity, last_uid) VALUES ('<mailbox>', '<folder>', <uidvalidity>, <last_uid>);"
```

Then start the services (its README step 4) and watch the first minute:

```bash
sudo systemctl enable --now las-worker las-beat las-ingest
journalctl -u las-ingest -u las-worker -f
```

The demo database starts empty: intake cases opened on the developer's machine do not exist here,
so a BD's reply to an old case is treated as a new mail.

---

## 9. Verify end to end

| Check | Expected |
|---|---|
| `http://<demo-host>/las-api/health` | `{"status":"ok", ..., "environment":"demo"}` |
| `http://<demo-host>/las/` | The LAS sign-in page |
| Sign in as a BD | Customer list loads |
| Open a customer page, press F5 | Page reloads (not a 404) |
| Upload a DPR with analysis (a customer with DPR Analysis paid) | Analysis shows progress, then the report; the PDF downloads |
| A BD in `INTAKE_BD_SENDERS` emails `las@integramicro.co.in` "status of <customer mobile>" | A reply within a minute or two; `journalctl -u las-worker` shows the handoff delivered |

---

## 10. Updating later

```bash
sudo -u postgres pg_dump -Fc las_demo > /var/backups/las_demo_$(date +%Y%m%d%H%M).dump   # first, always
cd /opt/las && sudo tar -xzf <new tarball> --strip-components=1      # keeps .env
sudo docker build -t las-backend:$(date +%Y%m%d) -t las-backend:latest .
sudo docker run --rm --network host --env-file /opt/las/.env las-backend:latest python scripts/migrate.py
sudo docker rm -f las-backend las-dpr-worker
# then the two `docker run -d` lines from step 6
sudo rm -rf /var/www/las/* && sudo cp -r /opt/las/frontend/dist/. /var/www/las/
```

LAS_Email, if it changed: copy `/opt/las/LAS_Email/.` over `/opt/las-email/` (its `.env` is not
in the tarball, so it is kept), then `sudo -u las make install migrate` and
`sudo systemctl restart las-worker las-beat las-ingest`.

**A setting changed** (`.env`): `docker restart` is **not** enough, because `--env-file` is read
when the container is created. `docker rm -f` and `docker run` again.

---

## 11. Rollback

```bash
sudo docker images las-backend                  # pick the previous date tag
sudo docker rm -f las-backend las-dpr-worker
# the two `docker run -d` lines from step 6, with :<previous-date> instead of :latest
```

Migrations only add things, so the previous image normally runs on the newer tables. If it does
not, restore the dump taken before the update (`pg_restore --clean -d las_demo <dump>`).

---

## 12. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `502 Bad Gateway` on `/las-api/...` | `las-backend` not running, or `PORT` is not 9000. `docker ps`, `docker logs las-backend`. |
| `404` on `/las-api/health`, container up | `CONTEXT_PATH` is not `/las-api`. Recreate the container (step 10). |
| `/las/` blank page | Files not under `/var/www/las/`. Dev tools: `/las/assets/...` must load with 200. |
| Refreshing an inner page gives `404` | The `try_files` line from `nginx-las.conf` is missing. |
| Upload fails with `413` | `client_max_body_size` missing from the `/las-api/` location. |
| Sign-in: vendor unreachable / timeout | The server can't reach `10.10.20.153:8080` (step 0). |
| Sign-in: SSL/certificate error | `VENDOR_VERIFY_SSL=false` missing. |
| Adding a customer fails with `ADMIN_...` | `VENDOR_ADMIN_USER_ID` / `VENDOR_ADMIN_PASSWORD` wrong. |
| Log says `SchemaNotReady`; DPR or intake features off | Migrations not run: the `scripts/migrate.py` line in step 6. |
| `password authentication failed for user core_app` | Password in `DPR_DATABASE_URL` differs from step 3. Fix `.env`, or `ALTER ROLE core_app PASSWORD '...'`. |
| DPR stays "queued" | `docker logs las-dpr-worker`: missing `DPR_*` settings, or Gemini unreachable. |
| PDF report fails to render | `docker logs las-dpr-worker` for WeasyPrint/Pango errors: the image is out of date; rebuild. |
| BD gets no reply to an email | `journalctl -u las-ingest -u las-worker`. Handoff `REJECTED`/`401`: `CORE_INTAKE_API_KEY` ≠ `INTAKE_API_KEY`. Sender not in both BD lists: they get "not a registered BD". |
| BDs get every reply twice | Two LAS_Email copies read the mailbox. Stop the other one. |
| Container keeps restarting | `docker logs <name>`: usually a typo in `.env`. |

Logs: `docker logs -f las-backend`, `docker logs -f las-dpr-worker`,
`journalctl -u las-ingest -u las-worker -u las-beat -f`, `/var/log/nginx/error.log`.
