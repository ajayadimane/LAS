# LAS Dashboard Middleware (FastAPI)

## What this project is
A FastAPI middleware (Backend-for-Frontend) between our LAS dashboard and a
third-party application's REST APIs. The third party owns the database and all
CRUD logic. **We have no database.** Every operation is a call to their API.

Dashboard -> this FastAPI app -> third-party LAS APIs

## Responsibilities of this service
- Expose clean, dashboard-friendly endpoints (our own request/response schemas).
- Call the third-party APIs and handle their authentication (keep secrets server-side).
- Map third-party errors to consistent HTTP errors for the dashboard.
- Apply timeouts, retries (read-only vendor endpoints only -- see Rules), and logging.
- Optionally cache read-heavy endpoints (short TTL).
- Aggregate multiple vendor calls into one endpoint where the dashboard needs it.

## Third-party API details
The vendor product is **iWallet**. There is no OpenAPI spec; the contract is 13 PDFs.

- Docs: `api_docs/` (13 PDFs, one per endpoint). `api_docs/ENDPOINTS.md` is the
  generated index -- read that first; the PDFs are the source of truth.
- Auth method: **Bearer JWT**. `POST /IdentityAccessMgmt/user/login` with
  `{userId, programMngrId, password}` returns `accessToken`, which goes in
  `Authorization: Bearer <token>` on every other endpoint. The login call itself
  sends no `Authorization` header. The sample token is RS256-signed.
  Token lifetime: **10 hours** (`exp - iat = 36000` on a real sandbox token,
  2026-10-26). It is a Keycloak token (realm `ewallet`); `preferred_username`
  is the userId, and **`groupId` tells a BD from a customer** (verified
  2026-10-28): `["LAS.admin"]` = BD, `["LAS.enduser"]` = customer
  (`BD_IAM_GROUP_NAME` / `CUSTOMER_IAM_GROUP_NAME`, `app/core/roles.py`).
  Login returns `role`, and refuses any other account (403
  `NOT_A_DASHBOARD_USER`); `/bd/*`, `/persons/*`, `/custom-fields*` and
  `/identity/users` need a BD token (`BD_ONLY`), `/customer/*` a customer
  token (`CUSTOMER_ONLY`). The claims are read unverified -- safe only because
  every such route also forwards the token to the vendor, which verifies it.
  Login also returns the `exp` claim as `expires_at`.
  The dashboard stores the token plus the userId (BD id or customer id).
  Refresh endpoint: **UNKNOWN - ask user** (none documented).
  Logout / revocation: **UNKNOWN - ask user** (none documented).
- Base URLs: sandbox vs production split is **UNKNOWN - ask user**. The sandbox
  moved to `https://10.10.20.153:8080` on 2026-10-27 (user); the docs and the
  captured payloads name the earlier `https://10.10.11.54:8080`. Both are
  office-network addresses.
  **TLS does not verify against an IP, and `VENDOR_VERIFY_SSL=false` is the wrong
  fix.** The host presents a genuine Sectigo-issued certificate for
  `CN=*.workflo.ai` (SANs `*.workflo.ai`, `workflo.ai`, valid to 2027-02-07), so:
  (a) connecting by IP can never match the SAN, and (b) the server sends only the
  leaf, omitting the Sectigo intermediate, so the chain fails to build
  (`verify return code 21`). Ask the vendor for the **hostname** under
  `workflo.ai` we should be calling, and ask them to serve the full chain. Until
  then local runs need `VENDOR_VERIFY_SSL=false`, which must not reach production.
- Rate limits: the vendor sits behind a **KrakenD gateway** (`x-krakend` response
  header) and **does** return 429. Verified 2026-10-14: omitting
  `X-Original-Forwarded-For` produces an immediate `429` with an empty body,
  reproducibly. The header is what partitions rate-limit buckets, so if we ever
  fail to forward the real end-user IP, every dashboard user shares one bucket.
  No `Retry-After` or `X-RateLimit-*` headers are sent. The actual quota is still
  **UNKNOWN - ask user**.
- Pagination style: **none documented**. List endpoints return a bare JSON array with
  no envelope and accept no page / limit / offset / cursor parameter. Behaviour on
  large result sets (truncation? hard cap?) is **UNKNOWN - ask user**.
- Error format: every documented error returns the same envelope --
  `timestamp`, `transactionId`, `correlationId`, `definedErrorCode`, `status`,
  `errorCode`, `errorMessage`, `debugMessage`.
  Verified against the live sandbox on 2026-10-14: `errorCode`,
  `definedErrorCode`, `errorMessage` and `debugMessage` all carry the **same
  SCREAMING_SNAKE machine code** (e.g. `INVALID_CREDENTIALS_TO_AUTHENTICATE_USER`,
  `PM_NOT_FOUND`, `PROGRAM_MANAGER_ID_CAN_NOT_BE_EMPTY`) - *not* the English prose
  the PDFs show. `exceptions.py` still maps on the **HTTP status**, which is
  accurate and sufficient; the codes are a bonus that make a code -> friendly
  message layer possible. Note `errorMessage` is therefore **not fit to show an end
  user as-is** - it is a code, not a sentence.
  Not every failure carries the envelope: a missing geolocation header returns
  `500` with a zero-length body, a rate-limited call returns `429` with a
  zero-length body, and an unrouted path returns plain text `404 page not found`.
  The client handles all three.
- Correlation: success bodies carry `transactionId` and `correlationId`, which the
  vendor says to retain for support and reconciliation. Log both on every call.

### Vendor conventions
- **Four headers are mandatory on every call**, including login:
  `Content-Type: application/json`, `X-Original-Forwarded-For` (the end user's client
  IP, forwarded from the dashboard request), `X-Geolocation-Latitude`,
  `X-Geolocation-Longitude`. Set these in `vendor_client.py`, not per call site.
  Where the dashboard gets the geolocation values is **UNKNOWN - ask user**.
- **Field-name trap:** the program manager id is spelled `programMngrId` on login,
  forgot-password, and open-wallet, but `programManagerId` everywhere else. Same
  value, two spellings -- the vendor's own docs flag this. Our schemas use one name;
  `vendor_client.py` maps to the right spelling per endpoint.
- **Two different "group" concepts.** IAM `groupName` is `<programManagerId>.admin`
  or `<programManagerId>.enduser`. Person `groupId` is a business group such as
  `Merchant`. They are not interchangeable.
- **Every read is a POST with a JSON body.** The vendor exposes no GET at all.
- **Person details come back flattened.** Verified 2026-10-27 on
  `getPersonDetailsByBrokerId` and `getPersonDetailsByPersonId` (the by-id body is
  identical to a list element, `brokerId.id` included): nested values arrive as dotted keys --
  `"customFields.GeoCheck"`, `"contactInformation.postalAddress.city"`,
  `"brokerId.id"`, `"currentStatus.personStatus"`. `vendor_value()` in
  `app/schemas/person.py` and `custom_field_values()` read both flat and nested.
  The body also carries `settlementAccount.*` and `searchKeyValues.SK1-8`; never
  pass those to the dashboard.
- **The real custom-field model (user, 2026-10-27).** One field per service --
  `DPR_Creation`, `DPR_Analysis`, `GeoCheck`, `Doc_Verification`
  (`ServiceConfig.field_id`; Value Addition has none) -- plus three **per
  customer**, not per service: `Doc_Status`, `Payment_Status`, `Payment_Mode`.
  One status per customer is deliberate (user-confirmed). `"NA"` (any case) or
  empty means not set; on a service field it means **not opted** (confirmed).
  Only `"NA"` has been seen live; the other values are unconfirmed (frontend:
  `src/lib/customerStatus.ts`). Used by `app/services/bd.py`
  (`GET /api/v1/bd/customers`).
- **Adding a customer** (`POST /api/v1/bd/customers`, user spec 2026-10-27) is
  person registration (`POST /customermgmt/person/`), wallet creation
  (`POST /regularWallet/`, `tagId` from config), then login creation
  (`POST /IdentityAccessMgmt/user/`: userName = mobileNumber = the mobile,
  email and names from the form, lastName `"NA"` if blank, groupName
  `CUSTOMER_IAM_GROUP_NAME`). The login's password is **generated by us**
  (`app/core/passwords.py`), returned once as `temporary_password`
  (`Cache-Control: no-store`), and stored nowhere. It is also **emailed** to
  the customer right after the login is created (and on the login retry):
  `app/services/mailer.py` (Zoho SMTP, `smtp.zoho.in:465`, from
  `las@integramicro.co.in`, `SMTP_PASSWORD` in `.env`, a Zoho app password)
  and `app/services/credentials_mail.py`. A failed or unconfigured send never
  fails the request: the response says `credentials_emailed` /
  `credentials_email_error`. It cannot be resent later (the password is gone).
  Tests force `smtp_password=None` so they never send real mail. SMS is
  future work. A login 409 means one exists and its password is unknown to us.
  **Login creation uses a super-admin token, not the BD's** (verified live
  2026-10-27). With a BD token the vendor returns `500` wrapping Keycloak's
  `403` (the IAM calls Keycloak's admin API with the caller's token). So
  `app/services/admin_session.py` signs in via
  `POST /IdentityAccessMgmt/user/superadmin` `{userId, password}`
  (`VENDOR_ADMIN_USER_ID/PASSWORD`, server-side only), caches the token
  in memory until shortly before `exp` (10 h; `service-account-ewallet-cli-client`
  with `manage-users`), and renews it once on a 401. **The superadmin call
  itself needs a signed-in user's bearer** -- without one the gateway returns
  401 with an empty body -- so it carries the BD's token. Admin failures are
  `502 ADMIN_*`, never 401 (that would sign the BD out). The full flow,
  including the customer signing in with the generated password, works live.
  `POST /api/v1/bd/customers/{id}/login` retries just that step. personId = the mobile number;
  brokerId = the signed-in BD; program manager, group, tag and country code come
  from config; every service and status custom field is sent as `"NA"` and
  updated after payment. **The settlement account and IFSC are a placeholder
  from config, the same for every customer** (user's instruction) -- replace
  before production. No write is retried; if the wallet or login fails the customer
  is still registered, the response says `wallet_opened` / `login_created: false`, and
  `POST /api/v1/bd/customers/{id}/wallet` retries just that step (409 = already
  open = success).
- **Trailing slashes matter.** Verified live 2026-10-27: `POST /customermgmt/person`
  returns **307** redirecting to `/customermgmt/person/`. Postman follows 307
  silently, so its slash-less paths look fine; httpx does not follow, and we
  deliberately never do (a redirected POST re-sends body and token). Use the
  PDFs' spelling: `/customermgmt/person/`, `/regularWallet/`,
  `/IdentityAccessMgmt/user/`, `/customermgmt/customField/programManager/`.
  Login and `/walletInfoRetrieval/...` reads take no slash. A redirect is logged
  as `vendor_redirect ... location=<path>`.
- **Editing a customer** (user spec 2026-10-28, verified live): one vendor
  `PUT` per detail, no trailing slash -- `/customermgmt/person/emailAddress`
  `{programManagerId, personId, emailAddress}` and `/customermgmt/person/address`
  (all five `address*` fields; the vendor replaces the whole address). Ours:
  `PUT /api/v1/bd/customers/{id}/email` and `/address`, ownership-checked,
  never retried; the dashboard sends only what changed. The email change is
  on the person record only, not the customer's login. Name and mobile have
  no update API yet (more APIs to come from the user).
- **A wallet's id is its owner's userId** (user, 2026-10-27): the BD id for a
  BD, the customer id for a customer. `GET /api/v1/wallet/balance` reads
  `…/walletTransferService/wallet/walletBalance` with that id and the config
  `tagId`; the middleware rejects any `wallet_id` other than the token's user
  (403), since the vendor does not check. Amounts are vendor floats; a missing
  one is null, never 0. This unblocks the walletBalance row in `ENDPOINTS.md`.
- **The BD id is the broker id.** A BD signs in with the same id their
  customers carry as `brokerId.id` (e.g. `97315`); the dashboard saves the login
  userId and sends it as `brokerId`.
- **Opting, payment and documents** (user 2026-10-27/28,
  `app/services/enrolment.py`, routes under `/api/v1/bd/customers/{id}/`).
  **Per-service fields** (`app/services/service_fields.py`): for service field
  `F` -- `F` lifecycle (`Opted` -> `Docs uploaded` -> `Approved`/`Rejected` ->
  `Report ready`, searchable), `F_Payment` (`Processing`/`Paid`), `F_PayMode`,
  `F_TxnId`, `F_Doc` -- the last four non-searchable, non-unique, created by
  `scripts/setup_custom_fields.py` (done on the sandbox 2026-10-28). The
  customer-level `Payment_Status`/`Payment_Mode`(`Mixed`)/`Doc_Status` are a
  searchable **summary recomputed on every write**. A new service = config
  entry + `setup_custom_fields.py --apply` (ask first: it changes the program
  manager's setup). All writes use `PUT /customermgmt/person/customFields`,
  which is all-or-nothing (verified). Amount = services + joining fee on the
  first payment only; services can be added later (Edit customer).
  **Pay now** (dummy QR): BD confirms receipt, no money moves. **Pay later**:
  both consents, claim (`Processing`) -> `PUT /transfer/systemAccountPayment`
  (pool -> `IntegraMain`, `pool_to_main`; never retried) -> `Paid` + `F_TxnId`.
  4xx releases the claim; 5xx/timeout leaves `Processing` and blocks payments
  (`PAYMENT_OUTCOME_UNKNOWN`). Documents: per service, once paid, until
  approved; base64 JSON in `F_Doc`, 2 MB cap. **Every customer-list call
  returns all custom fields, documents included: move documents to object
  storage (e.g. MinIO) before many customers have them** (only
  `app/services/documents.py` changes). The ₹795 test
  transfer (txn `7520906027847049216`) **arrived in IntegraMain** (user,
  2026-10-28), though its balance is not readable via walletBalance
  (`WALLET_DO_NOT_EXISTS` with tag1: likely a system account).
  **A customer opting in themselves** (`/api/v1/customer/...`, person id =
  the token's user, user 2026-10-28): the service goes to `Awaiting BD` (not
  opted, not in the summary, listed as `requests` on the BD list). Pay later:
  the customer promises to pay; the BD authorizes = stands guarantor
  (`bd_consent`), and the pool transfer runs then; or declines. Pay now: no BD
  approval in principle, but while the QR is a placeholder the BD confirms
  receipt (`CUSTOMER_PAY_NOW_NEEDS_CONFIRMATION=true`; turn off with a real
  payment confirmation). A refused transfer puts the request back.
  **Vendor gap, verified 2026-10-28:** a person only has the custom fields that
  existed when they were registered; updating a newer field returns
  `400 CUSTOM_FIELD_ID_NOT_EXISTS` (nothing written). So customers registered
  before 2026-10-28, and everyone when a future service is added, cannot use
  new fields until the vendor backfills or upserts them -- raise with vendor.
- Service prefixes: `/IdentityAccessMgmt` (identity), `/customermgmt` (persons and
  custom field definitions), `/regularWallet` (wallet creation),
  `/walletInfoRetrieval` (all reads).

## Tech stack
- Python 3.12, FastAPI, Uvicorn
- httpx.AsyncClient (one shared client created in app lifespan, not per request)
- Pydantic v2 + pydantic-settings for config from `.env`
- tenacity for retries
- pytest + pytest-asyncio + respx (mock vendor HTTP calls in tests)
- ruff for lint/format

## Project structure
```
app/
  main.py              # app factory, lifespan (create/close httpx client), routers
  core/
    config.py          # Settings (env vars)
    logging.py
    exceptions.py      # VendorError -> HTTPException mapping, exception handlers
  clients/
    vendor_client.py   # low-level HTTP wrapper: auth, timeouts, retries, error parsing
  services/            # business logic per domain; calls vendor_client, reshapes data
  schemas/             # Pydantic models: our API contracts (separate from vendor shapes)
  api/
    deps.py            # dependencies (get client, dashboard auth)
    v1/                # routers, one file per resource
tests/
.env.example
```

## Rules
- Routers stay thin: validate input, call a service, return a schema.
- Never return raw vendor JSON to the dashboard; map it into our schemas.
- Never hardcode secrets or URLs; everything comes from `config.py`.
- All vendor calls go through `vendor_client.py`; no httpx calls elsewhere.
- Every endpoint gets a test with the vendor mocked via respx.
- Log every vendor call with method, path, status, and duration. Never log secrets.
  Log `transactionId` and `correlationId` from the response body too.
- **Live vendor calls: standing permission (user, 2026-10-27), with limits.**
  Claude may call the sandbox at `10.10.20.153` (formerly `10.10.11.54`) to
  verify integrations -- including `scripts/sandbox_smoke.py` -- without asking
  each time:
  - **Reads and login:** go ahead. Stop after one failed login and report it;
    repeated failures may lock a real account (lockout policy undocumented).
  - **Writes that create or change records** (registration, wallet, custom
    field updates): test data only, and say exactly what was created.
  - **Anything that moves money** (transfers, payments, top-ups): still ask
    first, every time.
  Never print or commit credentials or tokens; they live in `.env`
  (`VENDOR_SMOKE_*`). Unit tests still mock the vendor with respx; captured
  real responses go in `tests/test_real_vendor_payloads.py` (or the test file
  for that feature).
- Retries: the vendor has no GET endpoints, so "idempotent GETs only" does not apply.
  Retry **only** endpoints under `/walletInfoRetrieval` -- they are POST but are
  read-only and side-effect free. Never retry `/customermgmt`, `/regularWallet`, or
  `/IdentityAccessMgmt` writes; the vendor documents no idempotency key.

## Dependencies
`pyproject.toml` declares version *ranges*; it is not what you install from.
`requirements.txt` (runtime) and `requirements-dev.txt` (runtime + dev) are the
locks: every package pinned to one version, each carrying the sha256 of every
artifact published for that version, so the lock is valid on any platform.
Installing from them is reproducible and pip verifies each download.
**Never hand-edit a lock file** -- regenerate it (see Commands).

To change a dependency: edit the range in `pyproject.toml`, re-lock, commit both.

## Commands
- Set up:
  ```
  python3 -m venv .venv && source .venv/bin/activate
  pip install -r requirements-dev.txt   # or requirements.txt for runtime only
  pip install -e . --no-deps            # the app itself; deps are pinned above
  ```
  `--no-deps` stops pip re-resolving against the ranges in `pyproject.toml`,
  which could pull a version the lock does not name. Add `--require-hashes` to
  the first command in CI to make a tampered or unpinned package a hard error.
- Run: `uvicorn app.main:app --reload`
- Test: `pytest -q`
- Lint: `ruff check . && ruff format .`
- Re-lock (after editing dependencies in `pyproject.toml`):
  ```
  python scripts/lock_dependencies.py
  ```
  By default it reuses the versions installed in the current environment, so
  the lock matches the set the tests are green on. `--fresh` resolves from
  scratch. (`pip-compile --generate-hashes` produces an equivalent file and is
  the more standard tool, but its resolver did not finish in a reasonable time
  against these ranges, so the script is the supported path here.)
- Live vendor smoke test: `python scripts/sandbox_smoke.py` -- see Rules
  for what may be run without asking.

## Frontend (`frontend/`)
React port of the colleague's demo (`LAS — Customer - BD portal.html`). Vite 5,
React 18, TypeScript, Tailwind 3.4 (theme tokens in `src/index.css`), React
Router, TanStack Query. Exact versions pinned in `package.json`; `package-lock.json`
is the lock.
- All network calls go through `src/api/client.ts` to our middleware at `/api/v1`,
  never to the vendor. In dev, Vite proxies `/api` and `/health` to
  `VITE_API_PROXY_TARGET` (default `http://localhost:8000`).
- `src/data/catalog.ts` (services, fees, UPI id) is placeholder data from the demo.
- **Context paths** (for the demo deployment): the backend's `CONTEXT_PATH`
  prefixes every route, `/docs` and `/openapi.json` included (empty = root).
  The UI's `VITE_BASE_PATH` sets Vite `base` and the router `basename`;
  `VITE_API_BASE_PATH` must equal the backend's `CONTEXT_PATH`. Both are
  build-time, so changing one needs a rebuild.
- Commands (run in `frontend/`): `npm install`, `npm run dev` (port 5173),
  `npm run build` (type-checks first), `npm run typecheck`.
