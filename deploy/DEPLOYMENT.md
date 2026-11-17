# LAS: demo deployment

Everything runs on one demo server, behind its existing nginx. Core LAS runs with Docker Compose
(`docker-compose.yaml` in the code's root); email intake (LAS_Email) runs under systemd.

```
Browser ─► https://<demo-host>/las/      ─► nginx ─► container las: Vite dev server (:8110)
        └► https://<demo-host>/las-api/  ─► nginx ─► container las: backend API (:8109)
                                                      ├► iWallet vendor (https://10.10.20.153:8080)
                                                      ├► Postgres (127.0.0.1:5432, schema core)
                                                      └► Zoho SMTP (all mail to BDs/customers)
DPR worker (child process of the API) ─► Postgres + Gemini   (DPR analysis, PDF reports)

Zoho mailbox (IMAP) ─► LAS_Email (systemd: las-ingest, las-worker, las-beat; Redis, ClamAV)
                         ├► Postgres (schema email) + Gemini
                         └► http://127.0.0.1:8109/las-api/internal/v1/intake/events  (never via nginx)
```

| Part | What it is | Where |
|---|---|---|
| Container `las` (compose service `fraud`) | Backend API (uvicorn, 8109), the DPR worker it starts, and the UI from source (`npm run dev`, 8110). The code is mounted from the host. | `/opt/las`, settings `/opt/las/.env` |
| LAS_Email | Email intake, Python venv + systemd | `/opt/las-email`, settings `/opt/las-email/.env` |
| Database | One Postgres database `las_demo`: schema `core` (Core), `email` (LAS_Email) | Local Postgres |

**Ports 8109 and 8110 listen on `0.0.0.0`: keep them closed in the firewall so only nginx
reaches them.** If nginx runs on this same server, change both `--host` values in
`docker-compose.yaml` to `127.0.0.1` instead. Why:
- the API trusts `X-Forwarded-For` from nginx and must not be reachable around it;
- the Vite dev server (5.4.11) has known file-read flaws, and the container mounts the whole code
  folder, `.env` included. Building the UI (`npm run build`, served as static files) removes this
  risk entirely.

> **Only one LAS_Email may read the mailbox.** Before starting it here (step 7), stop every other
> copy (the developer's machine included). Two readers process the same mail twice and send the
> BDs every reply twice.

---

## 0. Before you start (one-time checks)

```bash
docker-compose version    # Docker Compose v1 (`docker-compose`, with a hyphen)
nginx -v                  # nginx installed
psql --version            # PostgreSQL 14+ (else: sudo apt install postgresql)
python3 --version         # 3.10+ (for LAS_Email)
sudo ss -ltnp | grep -E ':8109|:8110|:5432'   # 8109, 8110 must be free; 5432 = Postgres
```

The server must reach the vendor (office network), Google Gemini, Zoho and the npm registry:

```bash
curl -k -s -o /dev/null -w "%{http_code}\n" https://10.10.20.153:8080            # 404 = good
curl -s -o /dev/null -w "%{http_code}\n" https://generativelanguage.googleapis.com # 404 = good
curl -s -o /dev/null -w "%{http_code}\n" https://registry.npmjs.org/              # 200 = good
timeout 5 bash -c '</dev/tcp/imap.zoho.in/993' && echo imap-ok
timeout 5 bash -c '</dev/tcp/smtp.zoho.in/465' && echo smtp-ok
```

A timeout on any of these: fix the network first. (The frontend container runs `npm ci` on every
start, so it needs the npm registry each time.)

---

## 1. What you receive

From the dev team:

1. **`las-code.zip`**: the code (backend, frontend source, `migrations/`, `deploy/`,
   `LAS_Email/`, `docker-compose.yaml`). The developer makes it with
   `git archive --format=zip -o las-code.zip HEAD`, so it holds no `.env`, no secrets, no
   `node_modules` and no virtualenvs.
2. **Secrets, sent separately and privately:** vendor admin and intake BD logins, the Zoho app
   passwords (SMTP and IMAP), the Gemini API key, the BD list. Never commit them, put them in an
   image, or paste them in chat.

You generate the rest yourself below (database passwords, encryption keys, the intake key).
Keep a copy of every value in the team's password manager: **the encryption keys cannot be
recovered**, and without them the stored data is unreadable.

---

## 2. Unpack

```bash
sudo mkdir -p /opt/las
sudo unzip -q las-code.zip -d /opt/las
ls /opt/las        # app  deploy  docker-compose.yaml  Dockerfile  frontend  LAS_Email  migrations ...
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

## 4. Backend settings: `/opt/las/.env`

Generate the keys:

```bash
python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"  # run twice: DPR_STAGING_ENCRYPTION_KEY, INTAKE_ENCRYPTION_KEY
openssl rand -hex 32     # run twice: INTAKE_API_KEY, OTP_TOKEN_SECRET
```

`DPR_WORKER_AUTOSTART` and the frontend's `VITE_*` paths are set in `docker-compose.yaml`;
the ports are in its command. The rest:

```
ENVIRONMENT=demo
LOG_LEVEL=INFO
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

# DPR analysis (the worker the API starts)
DPR_AGENTIC_ENABLED=true
DPR_AGENTIC_GEMINI_API_KEY=<from dev team>
DPR_AGENTIC_GEMINI_MODEL=gemini-3.8-flash
DPR_AGENTIC_VISION_ENABLED=true
DPR_AGENTIC_VISION_MAX_PAGES=100
DPR_STAGING_ENCRYPTION_KEY=<generated>
DPR_GEMINI_PRICE_INPUT_PER_MTOK_USD=0.75
DPR_GEMINI_PRICE_OUTPUT_PER_MTOK_USD=3.75
DPR_GEMINI_PRICE_CACHED_INPUT_PER_MTOK_USD=0.075
DPR_GEMINI_PRICE_SEARCH_PER_1K_USD=14

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

Everything else has a working default; `/opt/las/.env.example` lists them all. The
`VENDOR_SMOKE_*` settings are for a developer script only: leave them out here.

**Placeholders the demo runs with** (fine for a demo, not for production): the Pay now UPI id
(`lasservices@upi`) and the settlement account/IFSC sent for every customer. **Pay later moves real
sandbox money** from the pool account.

---

## 5. Start Core LAS (Docker Compose)

The base image `python3.10-tar-ubuntu-20.04` must already exist on the server (`docker images`).

```bash
cd /opt/las
sudo docker-compose build

# Create/upgrade the tables (as core_owner). Every deploy; safe to repeat.
sudo docker-compose run --rm fraud python scripts/migrate.py

sudo docker-compose up -d
```

This starts the container `las` (`restart: always`). On every start it runs `npm ci` for the
frontend first, so give it a minute. The API starts the DPR worker itself and restarts it if it
crashes; if either the API or the dev server stops, the whole container restarts.

Check:

```bash
sudo docker-compose ps                              # las: Up
curl http://127.0.0.1:8109/las-api/health           # {"status":"ok",...,"environment":"demo"}
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8110/las/   # 200
sudo docker-compose logs | grep -i -E "error|schema|intake" | tail
sudo docker-compose logs | grep dpr_worker | tail   # "dpr_worker_autostarted", no "missing_settings"
sudo docker-compose logs | grep -i vite | tail      # "VITE v5.4.11 ready"
```

---

## 6. nginx

Inside the demo host's `server { ... }` block:

```nginx
include /opt/las/deploy/nginx-las.conf;
```

```bash
sudo nginx -t && sudo systemctl reload nginx
curl -s -o /dev/null -w "%{http_code}\n" https://<demo-host>/las-api/internal/v1/intake/events   # 404: blocked, as intended
```

`nginx-las.conf` assumes ports 8110 (frontend) and 8109 (backend), as in `docker-compose.yaml`.

---

## 7. Email intake (LAS_Email)

LAS_Email's own runbook is `/opt/las/LAS_Email/deploy/README.md`. Follow its steps 1, 2 and 4
with these demo specifics. Its step 3 (database) is replaced by what is below.

**Code:** instead of `git clone`, copy it out of the zip:

```bash
sudo useradd -r -m -d /opt/las-email las 2>/dev/null || true
sudo cp -r /opt/las/LAS_Email/. /opt/las-email/ && sudo chown -R las:las /opt/las-email
```

**`/opt/las-email/.env`**, in addition to what its README lists:

```
DATABASE_URL=postgresql+asyncpg://email_app:<email_app password>@127.0.0.1:5432/las_demo
DB_SCHEMA=email
MIGRATION_DATABASE_URL=postgresql+asyncpg://email_owner:<email_owner password>@127.0.0.1:5432/las_demo
CORE_INTAKE_URL=http://127.0.0.1:8109/las-api/internal/v1/intake/events
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

## 8. Verify end to end

| Check | Expected |
|---|---|
| `https://<demo-host>/las-api/health` | `{"status":"ok", ..., "environment":"demo"}` |
| `https://<demo-host>/las/` | The LAS sign-in page |
| Sign in as a BD | Customer list loads |
| Open a customer page, press F5 | Page reloads (not a 404) |
| Upload a DPR with analysis (a customer with DPR Analysis paid) | Analysis shows progress, then the report; the PDF downloads |
| A BD in `INTAKE_BD_SENDERS` emails `las@integramicro.co.in` "status of <customer mobile>" | A reply within a minute or two; `journalctl -u las-worker` shows the handoff delivered |

---

## 9. Updating later

```bash
sudo -u postgres pg_dump -Fc las_demo > /var/backups/las_demo_$(date +%Y%m%d%H%M).dump   # first, always
sudo cp -a /opt/las /opt/las.previous                                                       # for rollback
cd /opt/las && sudo unzip -oq <new las-code.zip> -d /opt/las      # keeps .env (not in the zip)
sudo docker-compose build                                         # only rebuilds if requirements.txt changed
sudo docker-compose run --rm fraud python scripts/migrate.py
sudo docker-compose restart
```

The code is mounted, not baked into the image, so a restart picks up the new code.

LAS_Email, if it changed: copy `/opt/las/LAS_Email/.` over `/opt/las-email/` (its `.env` is not
in the zip, so it is kept), then `sudo -u las make install migrate` and
`sudo systemctl restart las-worker las-beat las-ingest`.

**A setting changed** (`.env`): `sudo docker-compose up -d --force-recreate`.
A plain `docker restart` does not re-read `.env`.

---

## 10. Rollback

```bash
sudo docker-compose down
sudo mv /opt/las /opt/las.failed && sudo mv /opt/las.previous /opt/las
cd /opt/las && sudo docker-compose up -d --build
```

Migrations only add things, so the previous image normally runs on the newer tables. If it does
not, restore the dump taken before the update (`pg_restore --clean -d las_demo <dump>`).

---

## 11. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `502 Bad Gateway` on `/las/` | The dev server is not up yet (`npm ci` still running) or failed: `docker-compose logs`. |
| `502 Bad Gateway` on `/las-api/...` | The API is not running: `docker-compose ps`, `docker-compose logs`. |
| The container keeps restarting with `npm` network errors | The server cannot reach `registry.npmjs.org` (step 0). |
| `/las/` blank page, or assets 404 | `VITE_BASE_PATH` is not `/las` (set in `docker-compose.yaml`), or `/las/` in nginx has a trailing slash on `proxy_pass`. |
| Browser console: websocket errors | The `Upgrade`/`Connection` lines are missing from `/las/` in nginx. Harmless, but noisy. |
| `404` on `/las-api/health`, container up | `CONTEXT_PATH` is not `/las-api`. |
| Upload fails with `413` | `client_max_body_size` missing from the `/las-api/` location. |
| Sign-in: vendor unreachable / timeout | The server can't reach `10.10.20.153:8080` (step 0). |
| Sign-in: SSL/certificate error | `VENDOR_VERIFY_SSL=false` missing. |
| Adding a customer fails with `ADMIN_...` | `VENDOR_ADMIN_USER_ID` / `VENDOR_ADMIN_PASSWORD` wrong. |
| Log says `SchemaNotReady`; DPR or intake features off | Migrations not run: the `scripts/migrate.py` line in step 5. |
| `password authentication failed for user core_app` | Password in `DPR_DATABASE_URL` differs from step 3. Fix `.env`, or `ALTER ROLE core_app PASSWORD '...'`. |
| DPR stays "queued" | `docker-compose logs \| grep dpr_worker`: `missing_settings` (a `DPR_*` setting), or Gemini unreachable. |
| BD gets no reply to an email | `journalctl -u las-ingest -u las-worker`. Handoff `REJECTED`/`401`: `CORE_INTAKE_API_KEY` ≠ `INTAKE_API_KEY`. Sender not in both BD lists: they get "not a registered BD". |
| BDs get every reply twice | Two LAS_Email copies read the mailbox. Stop the other one. |
| A container keeps restarting | `docker-compose logs <name>`: usually a typo in `.env`. |

Logs: `docker-compose logs -f`, `journalctl -u las-ingest -u las-worker -u las-beat -f`,
`/var/log/nginx/error.log`.
