# iWallet Vendor API — Endpoint Index

Generated from the 13 PDFs in this directory. **The PDFs are the source of truth**;
this file is a navigable index. If they disagree, the PDF wins — and please fix this
file.

- **Host (only one documented):** `https://10.10.11.54:8080`
- **Auth:** Bearer JWT from `POST /IdentityAccessMgmt/user/login`
- **Content type:** `application/json` on every request and response
- **Reads are POSTs.** The vendor exposes no `GET` endpoint anywhere.

## All endpoints

| Resource | Method | Path | Purpose | Required params |
|---|---|---|---|---|
| identity | POST | `/IdentityAccessMgmt/user/login` | Authenticate; returns the access token used by every other endpoint | `userId`, `programMngrId`, `password` |
| identity | POST | `/IdentityAccessMgmt/user/` | Create an IAM user under a program manager and group | `userName`, `email`, `firstName`, `lastName`, `programManagerId`, `groupName`, `mobileCountryCode`, `mobileNumber`, `twoFactorAuthCodeType`, `twoFactorAuthCodeDeliveryChannel`, `password` |
| identity | POST | `/IdentityAccessMgmt/user/password/forgot` | Trigger a password-reset email; user completes reset on the IdP page | `userId`, `programMngrId`, `email` |
| persons | POST | `/customermgmt/person/` | Register a person; also creates an empty KYC document | `emailAddress`, `addressStreetAddress`, `addressCity`, `addressStateProvince`, `addressPostalCode`, `addressCountryCode`, `mobileNumber`, `mobileCountryCode`, `firstName`, `settlementAccountNo`, `settlementAccountIfscCode`, `programManagerId`, `groupId`, `customFields` |
| persons | POST | `/walletInfoRetrieval/customerMgmtService/getPersonDetailsByPersonId` | Fetch one person's details | `personId`, `programManagerId` |
| persons | POST | `/walletInfoRetrieval/customerMgmtService/getPersonDetailsByBrokerId` | List all persons registered under a broker | `brokerId`, `programManagerId` |
| custom-fields | POST | `/customermgmt/customField/programManager/` | Define a custom field for a program manager | `programManagerId`, `customFieldId`, `customFieldDataType`, `customFieldName`, `customFieldDescription`, `isSearchable`, `isUnique`, `searchScope` |
| custom-fields | PUT | `/customermgmt/person/customField/value` | Update one custom field value on a person | `personId`, `programManagerId`, `customFieldId`, `customFieldValue` |
| custom-fields | PUT | `/customermgmt/person/customFields` | Update many custom field values on a person in one call | `personId`, `programManagerId`, `customFields` |
| custom-fields | POST | `/walletInfoRetrieval/customerMgmtService/customFields` | List every custom field defined for a program manager | `programManagerId` |
| wallets | POST | `/regularWallet/` | Open a regular wallet for a person (one wallet per tag) | `programMngrId`, `personId`, `groupId`, `tagId` |
| wallets | POST | `/walletInfoRetrieval/walletMgmtService/getWalletDetailsByMobileNumber` | List the wallets of the person holding a mobile number | `mobileNumber`, `programManagerId` |
| wallets | POST | `/walletInfoRetrieval/walletTransferService/wallet/walletBalance` | Read a wallet's total / available / locked balance | `walletId`, `tagId`, `programManagerId` |

## Implementation status

10 of the 13 vendor endpoints are wired up. Our dashboard-facing route is given
where it exists; the vendor path is what we call underneath.

| Vendor endpoint | Our route | Status |
|---|---|---|
| `POST /IdentityAccessMgmt/user/login` | `POST /api/v1/identity/login` | done |
| `POST /IdentityAccessMgmt/user/` | `POST /api/v1/identity/users` | done |
| `POST /IdentityAccessMgmt/user/password/forgot` | `POST /api/v1/identity/password/forgot` | done |
| `POST /customermgmt/person/` | `POST /api/v1/persons` | done |
| `…/getPersonDetailsByPersonId` | `GET /api/v1/persons/{person_id}` | done |
| `…/getPersonDetailsByBrokerId` | `GET /api/v1/persons?broker_id=` | done |
| `POST /customermgmt/customField/programManager/` | `POST /api/v1/custom-fields` | done |
| `…/customerMgmtService/customFields` | `GET /api/v1/custom-fields` | done |
| `PUT /customermgmt/person/customField/value` | `PUT /api/v1/persons/{id}/custom-fields/{field_id}` | done |
| `PUT /customermgmt/person/customFields` | `PUT /api/v1/persons/{id}/custom-fields` | done |
| `POST /regularWallet/` | -- | **not started** |
| `…/getWalletDetailsByMobileNumber` | -- | **not started** |
| `…/wallet/walletBalance` | -- | **blocked**, see below |

Wallets are untouched. `walletBalance` needs a `walletId` that no documented
endpoint is specified to return; `getWalletDetailsByMobileNumber` probably
supplies it, but its response body is undocumented, so neither can be built
against a known contract. Open question 3 covers this.

Note the vendor's reads are POSTs; we expose them as GETs, because that is
what they are and the dashboard should not inherit the vendor's shape.

## Responses and error codes

| Path | Success | Response shape | Documented failures | Source PDF |
|---|---|---|---|---|
| `/IdentityAccessMgmt/user/login` | 200 | `{transactionId, correlationId, timeStamp, accessToken}` | 404, 500 | `IAM_Login_API_Documentation.pdf` |
| `/IdentityAccessMgmt/user/` | 201 | ack only | 401, 409 (name/email taken), 500 | `IAM_AddUser_API_Documentation.pdf` |
| `/IdentityAccessMgmt/user/password/forgot` | 201 | ack only | 401, 404, 500 | `IAM_ForgotPassword_API_Documentation.pdf` |
| `/customermgmt/person/` | 201 | ack only | 401, 404, 409 (already registered), 500 | `Person_Registration_API_Documentation.pdf` |
| `/customermgmt/customField/programManager/` | 201 | ack only | 401, 404, 409 (field exists), 500 | `CustomField_ProgramManager_API_Documentation.pdf` |
| `/customermgmt/person/customField/value` | 200 | ack only | 401, 404, 500 | `PersonCustomField_Single_Update_API_Documentation.pdf` |
| `/customermgmt/person/customFields` | 200 | ack only | 401, 404, 500 | `PersonCustomFields_Multiple_Update_API_Documentation.pdf` |
| `/regularWallet/` | 201 | ack only | 401, 404, 409 (wallet exists), 500 | `RegularWallet_Open_API_Documentation.pdf` |
| `…/customerMgmtService/customFields` | 200 | JSON array — **fields undocumented** | 401, 404, 500 | `WalletInfo_CustomFields_API_Documentation.pdf` |
| `…/customerMgmtService/getPersonDetailsByPersonId` | 200 | object: `personId, firstName, lastName, programManagerId, groupId, mobileNumber, emailAddress, cifNumber` | 401, 404, 500 | `WalletInfo_PersonDetailsByPersonId_API_Documentation.pdf` |
| `…/customerMgmtService/getPersonDetailsByBrokerId` | 200 | array of the object above | 401, 404, 500 | `WalletInfo_PersonDetailsByBrokerId_API_Documentation.pdf` |
| `…/walletMgmtService/getWalletDetailsByMobileNumber` | 200 | JSON array — **fields undocumented** | 401, 404, 500 | `WalletInfo_WalletDetailsByMobileNumber_API_Documentation.pdf` |
| `…/walletTransferService/wallet/walletBalance` | 200 | `{totalAmount, availableAmount, lockedAmount}` (numbers) | 401, 404, 500 | `WalletInfo_WalletBalance_API_Documentation.pdf` |

"ack only" means `{transactionId, correlationId, timeStamp}` and nothing else —
the vendor returns no id or representation of the thing it just created.

## Mandatory headers (every endpoint)

| Header | Value | Notes |
|---|---|---|
| `Authorization` | `Bearer <access-token>` | All endpoints **except** login |
| `Content-Type` | `application/json` | |
| `X-Original-Forwarded-For` | end user's client IP | Forward from the dashboard request |
| `X-Geolocation-Latitude` | e.g. `12.9716` | Source of this value is an open question |
| `X-Geolocation-Longitude` | e.g. `77.5946` | Source of this value is an open question |

## Error envelope

Identical on every endpoint:

```json
{
  "timestamp": "30-09-2026 06:20:47",
  "transactionId": "7510946730856935424",
  "correlationId": "string",
  "definedErrorCode": "The program manager not found",
  "status": 404,
  "errorCode": "The program manager not found",
  "errorMessage": "The program manager not found",
  "debugMessage": "The program manager not found"
}
```

**Corrected 2026-10-14 against the live sandbox.** The PDFs show English prose
("The program manager not found"). The real API returns a SCREAMING_SNAKE machine
code, repeated identically in all four message fields:

```json
{
  "correlationId": "be924425-87ec-45d0-a4cc-b5336a814e0c",
  "debugMessage": "INVALID_CREDENTIALS_TO_AUTHENTICATE_USER",
  "definedErrorCode": "INVALID_CREDENTIALS_TO_AUTHENTICATE_USER",
  "errorCode": "INVALID_CREDENTIALS_TO_AUTHENTICATE_USER",
  "errorMessage": "INVALID_CREDENTIALS_TO_AUTHENTICATE_USER",
  "status": 400,
  "timestamp": "14-10-2026 10:03:23",
  "transactionId": "7516076178871377920"
}
```

Codes observed so far: `INVALID_CREDENTIALS_TO_AUTHENTICATE_USER` (400),
`PM_NOT_FOUND` (404), `PROGRAM_MANAGER_ID_CAN_NOT_BE_EMPTY` (400).

We still map to our HTTP errors on the **HTTP status**, which is accurate. But
`errorMessage` is a code, not a sentence, so it must not be shown to an end user
unmodified.

Observed in reality but not in any PDF: the envelope's `status` **did** match the
HTTP status on every live response, and `correlationId` is a UUID (the PDFs show a
numeric snowflake). Some failures carry no envelope at all - see below.

Note the timestamp mismatch: errors use `timestamp` as `dd-MM-yyyy HH:mm:ss`, success
bodies use `timeStamp` as ISO-8601 (`2025-06-30T07:07:03.512Z`). Confirmed live.

### Failures that carry no envelope

| Trigger | Response | Body |
|---|---|---|
| Missing `X-Geolocation-*` headers | `500` | empty (0 bytes) |
| Missing `X-Original-Forwarded-For` | `429` | empty (0 bytes) |
| Unrouted path | `404` | plain text `404 page not found` |

The gateway is **KrakenD** (`x-krakend` header). Gateway-level rejections bypass
the backend and therefore bypass the envelope entirely.

## Field validation reference

Constraints the PDFs state explicitly. These drive the Pydantic validators in
`app/schemas/`.

| Field | Constraint |
|---|---|
| `personId` | optional on registration; 1–50 chars, no spaces. Defaults to the mobile number if omitted |
| `emailAddress` | valid email, max 100 chars |
| `addressStreetAddress`, `addressCity` | max 100 chars |
| `addressStateProvince` | 2–100 chars |
| `addressPostalCode` | 5–12 chars |
| `addressCountryCode` | exactly 2 chars |
| `mobileNumber` | exactly 10 digits; unique per program manager |
| `mobileCountryCode` | exactly 2 digits (see open questions) |
| `firstName` | 1–50 chars |
| `lastName` | optional, 1–50 chars |
| `settlementAccountNo` | 9–18 chars |
| `settlementAccountIfscCode` | exactly 11 chars |
| `brokerId` | required only when the group is configured to need a broker |
| `customFields` | map of `customFieldId` → value. On registration, **every** field defined for the program manager must be present |
| `customFieldDataType` | `Text` or `List`, case-insensitive |
| `searchScope` | `NA`, `GLOBAL`, or `PROGRAM_MANAGER` |
| `groupName` (IAM) | `<programManagerId>.admin` or `<programManagerId>.enduser` |

## Open questions

Raised against the 13 PDFs as of 2026-10-14. Nothing here is answered by the docs.

### 1. There is no LAS domain in these docs

This is the big one. The service is an **LAS** dashboard BFF, but not one of the 13
endpoints covers loans, collateral, securities, pledge, lien, disbursement, repayment,
margin, or interest. The entire documented surface is identity, persons, custom fields,
and wallets. `lockedAmount` on the balance response is the only lien-shaped artefact in
the whole set.

Either a second document set exists and was not provided, or the LAS layer is ours to
build on top of wallets — those are very different projects. **Needs an answer before
any service-layer design.**

### 2. Missing CRUD in the documented domain

- No "list persons for a program manager" — only by `personId` or `brokerId`.
- No person search, despite custom fields carrying `isSearchable` and `searchScope`
  (including a `GLOBAL` scope). What consumes those flags?
- No update to a person's core fields (name, address, mobile, settlement account).
  Only custom fields are updatable.
- No deactivate / close / delete for a person or a wallet.
- No transaction history, and no credit / debit / transfer — even though the balance
  endpoint lives under `walletTransferService`.
- No KYC endpoints, though person registration says it "creates an empty KYC document".
- No program-manager or group CRUD; both are preconditions for nearly every call.

### 3. `walletId` has no documented source

`walletBalance` requires `walletId` (format `919824561458@integra`), but no endpoint is
documented to return one. `getWalletDetailsByMobileNumber` presumably does — its
response body is undocumented. Without this, the balance endpoint is uncallable.

### 4. Undocumented response shapes

`customFields` (list) and `getWalletDetailsByMobileNumber` say only "a JSON array" with
no field list. We cannot write response schemas for either.

### 5. Inconsistencies

- `programMngrId` (login, forgot-password, open-wallet) vs `programManagerId`
  (everything else). The vendor acknowledges this in the PDFs but has not fixed it.
- `userName` on add-user vs `userId` on login and forgot-password. Same identifier?
- Every PDF's error sample is the same copy-pasted 404 "program manager not found",
  even where the status table lists 401 or 409. The 401 and 409 body shapes are
  therefore **unverified** — confirm before relying on them.
- `mobileCountryCode` is specified as "exactly 2 digits", which excludes `+1` (US/CA)
  and every 3-digit code. Is this an India-only deployment, or is the doc wrong?
- `personId` defaults to the mobile number, but the open-wallet example uses
  `919824561458` (country code prefixed) while registration uses a bare `9824561458`
  and person-lookup uses `1234567890`. Which form is the default?
- Login's status table lists only 200/404/500 — no 401. What does a wrong password
  return?
- Login's example uses `programMngrId: "LAS"` (uppercase) where every other PDF uses
  `ibkart`/`uppcl` (lowercase). Are program manager ids case-sensitive?

### 6. Behavioural questions

- **Forgot-password requires a Bearer token**, so a locked-out user cannot self-serve.
  Is this admin-initiated only? If so, the dashboard flow needs rethinking.
- `customFieldDataType: List` is permitted, but nothing documents how to supply the
  allowed values, or how a List value is encoded on write.
- Person registration requires **every** custom field defined for the program manager.
  That forces a list-custom-fields call first — a natural aggregation point in
  `app/services/`.
- Geolocation headers are mandatory on every call. Does the dashboard send real user
  coordinates, or do we send a fixed server location?
- Token lifetime, refresh, and revocation are all undocumented. Our client needs a
  re-login-on-401 strategy until we know.
- The host is an RFC-1918 address. Is it reachable from where we deploy, and is its TLS
  certificate publicly trusted or self-signed?
- No rate limits or 429 are documented. Is there throttling we should back off from?

### 7. Answered by live testing on 2026-10-14

Verified with `scripts/sandbox_smoke.py` and direct probes against
`https://10.10.11.54:8080`. These supersede the PDFs where they disagree.

| Question | Answer |
|---|---|
| Is the host reachable? | Yes, from the office network. |
| Is the TLS cert self-signed? | **No.** Genuine Sectigo cert, `CN=*.workflo.ai`, SANs `*.workflo.ai` + `workflo.ai`, valid to 2027-02-07. |
| Why does TLS fail then? | Two reasons: we connect by **IP**, which no SAN covers; and the server sends only the leaf, omitting the Sectigo intermediate (`verify return code 21`). |
| What does a wrong password return? | `400` + `INVALID_CREDENTIALS_TO_AUTHENTICATE_USER`. The login PDF lists only 200/404/500, so **400 is undocumented**. |
| Does it distinguish unknown-user from wrong-password? | No - a nonexistent user returns the same code. Good (no user enumeration). |
| Is `errorCode` prose or a code? | A **machine code**. The PDFs are wrong. |
| Does envelope `status` match the HTTP status? | Yes on every live response, unlike every PDF sample. |
| Are rate limits real? | Yes. `429` is returned, and the gateway is KrakenD. |
| Are the geolocation headers really mandatory? | Yes - omitting them returns `500` with an empty body. |
| Is `X-Original-Forwarded-For` really mandatory? | Yes, and harder: omitting it returns `429` immediately. It keys the rate-limit bucket. |
| Is the `programMngrId` spelling split real? | **Yes.** Sending `programManagerId` to login returns `400 PROGRAM_MANAGER_ID_CAN_NOT_BE_EMPTY`. |

Still open: the token TTL (needs a successful login), the actual rate-limit quota,
and the correct `workflo.ai` hostname to call instead of the raw IP.
