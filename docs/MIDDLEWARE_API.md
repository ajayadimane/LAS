# LAS Middleware API reference

Every endpoint the LAS middleware (FastAPI) exposes to the dashboard, with a
sample request for each and the iWallet (vendor) API it calls.

Last updated 2026-10-28. The live, always-current version is the app's own
OpenAPI page: run the backend and open `http://localhost:8000/docs`.

## Conventions

- **Base URL:** `http://<host>:8000/api/v1` (only `/health` sits outside `/api/v1`).
- **Auth:** every endpoint except sign-in, forgot password and `/health` needs
  `Authorization: Bearer <access_token>` (the token from sign-in).
- **Roles:** the token says whether the user is a **BD** (`LAS.admin`) or a
  **customer** (`LAS.enduser`).
  - `/bd/*`, `/persons*`, `/custom-fields*` and `/identity/users` are **BD only**.
  - `/customer/*` is **customer only**.
  - The wrong role gets `403` with code `BD_ONLY` or `CUSTOMER_ONLY`.
- **`broker_id` query parameter** (BD routes): optional. It defaults to the
  signed-in BD and, if sent, must be that BD.
- **Ownership:** a BD only sees and changes their own customers. Anyone else's
  customer returns `404 Customer not found`.
- **Errors** all have one shape:

  ```json
  { "error": { "code": "PM_NOT_FOUND", "message": "Human-readable message",
               "transaction_id": "7521…", "correlation_id": "a240f194-…" } }
  ```

  Validation errors (bad input, rejected before reaching the vendor) are `422`.
- **Writes are never retried.** Only read-only calls are retried.
- **Service codes:**

  | Code | Name | Fee (₹) |
  |---|---|---|
  | `DPR_GENERATION` | DPR Generation | 2,000 |
  | `DPR_ANALYSIS` | DPR Analysis | 1,000 |
  | `GEO_CHECK` | Geo Check | 500 |
  | `DOC_VERIFICATION` | Doc Verification | 500 |
  | `VALUE_ADDITION` | Value Addition | 500 (paid on a partner portal, not here) |

  A one-time joining fee of ₹295 is added to the customer's first payment.

## Contents

| # | Method | Path | Who | Vendor API called |
|---|---|---|---|---|
| 1 | GET | `/health` | anyone | none |
| 2 | POST | `/identity/login` | anyone | `POST /IdentityAccessMgmt/user/login` |
| 3 | POST | `/identity/password/forgot` | anyone | `POST /IdentityAccessMgmt/user/password/forgot` |
| 4 | POST | `/identity/users` | BD | `POST /IdentityAccessMgmt/user/` |
| 5 | GET | `/services` | signed in | none (from config) |
| 6 | GET | `/wallet/balance` | signed in | `POST /walletInfoRetrieval/walletTransferService/wallet/walletBalance` |
| 7 | GET | `/bd/customers` | BD | `POST /walletInfoRetrieval/customerMgmtService/getPersonDetailsByBrokerId` |
| 8 | POST | `/bd/customers` | BD | register, open wallet, create login (3 calls) |
| 9 | GET | `/bd/customers/{person_id}` | BD | `POST …/getPersonDetailsByPersonId` |
| 10 | POST | `/bd/customers/{person_id}/wallet` | BD | `POST /regularWallet/` |
| 11 | POST | `/bd/customers/{person_id}/login` | BD | `POST /IdentityAccessMgmt/user/` (super-admin token) |
| 12 | PUT | `/bd/customers/{person_id}/email` | BD | `PUT /customermgmt/person/emailAddress` |
| 13 | PUT | `/bd/customers/{person_id}/address` | BD | `PUT /customermgmt/person/address` |
| 14 | GET | `/bd/customers/{person_id}/services` | BD | `POST …/getPersonDetailsByPersonId` |
| 15 | POST | `/bd/customers/{person_id}/payment` | BD | `PUT /customermgmt/person/customFields`, plus `PUT /transfer/systemAccountPayment` for Pay later |
| 16 | POST | `/bd/customers/{person_id}/services/{code}/authorize` | BD | same as 15 |
| 17 | POST | `/bd/customers/{person_id}/services/{code}/decline` | BD | `PUT /customermgmt/person/customFields` |
| 18 | PUT | `/bd/customers/{person_id}/services/{code}/document` | BD | `PUT /customermgmt/person/customFields` |
| 19 | GET | `/bd/customers/{person_id}/services/{code}/document` | BD | `POST …/getPersonDetailsByPersonId` |
| 20 | GET | `/customer/profile` | customer | `POST …/getPersonDetailsByPersonId` |
| 21 | GET | `/customer/services` | customer | `POST …/getPersonDetailsByPersonId` |
| 22 | POST | `/customer/services/request` | customer | `PUT /customermgmt/person/customFields` |
| 23 | PUT | `/customer/services/{code}/document` | customer | `PUT /customermgmt/person/customFields` |
| 24 | GET | `/customer/services/{code}/document` | customer | `POST …/getPersonDetailsByPersonId` |
| 25 | POST | `/persons` | BD | `POST /customermgmt/person/` |
| 26 | GET | `/persons` | BD | `POST …/getPersonDetailsByBrokerId` |
| 27 | GET | `/persons/{person_id}` | BD | `POST …/getPersonDetailsByPersonId` |
| 28 | POST | `/custom-fields` | BD | `POST /customermgmt/customField/programManager/` |
| 29 | GET | `/custom-fields` | BD | `POST /walletInfoRetrieval/customerMgmtService/customFields` |
| 30 | PUT | `/persons/{person_id}/custom-fields/{custom_field_id}` | BD | `PUT /customermgmt/person/customField/value` |
| 31 | PUT | `/persons/{person_id}/custom-fields` | BD | `PUT /customermgmt/person/customFields` |
| 32 | GET | `/otp/settings` | anyone | none (from config) |
| 33 | POST | `/otp/send` | signed in | `POST …/getPersonDetailsByPersonId`, then OTP service `POST /ibc/v1/otp/generate` |
| 34 | POST | `/otp/verify` | signed in | OTP service `POST /ibc/v1/otp/verify` |

Endpoints 1–24 and 32–34 are what the dashboard uses. Endpoints 25–31 are lower-level
building blocks kept for admin and support use.

---

## Health and sign-in

### 1. Health check
`GET /health`. No body, no token.

```json
{ "status": "ok", "app": "LAS Dashboard Middleware", "environment": "local" }
```

### 2. Sign in
`POST /api/v1/identity/login`. No token needed.

```json
{
  "user_id": "9000000903",
  "password": "<password>"
}
```

`program_manager_id` is optional and defaults to `LAS`. The response carries
the token and the user's role:

```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIs…",
  "token_type": "Bearer",
  "user_id": "9000000903",
  "role": "customer",
  "expires_in": 36000,
  "expires_at": 1761683400,
  "transaction_id": "7521…",
  "correlation_id": "…"
}
```

`role` is `bd` or `customer`. Any other account gets `403 NOT_A_DASHBOARD_USER`.

### 3. Forgot password
`POST /api/v1/identity/password/forgot`. No token needed. Returns `202`.

```json
{
  "user_id": "9000000903",
  "email": "test@gmail.com"
}
```

### 4. Create an IAM user (low level)
`POST /api/v1/identity/users`. BD only. The dashboard creates customer
logins through endpoint 8 or 11 instead.

```json
{
  "user_name": "9000000903",
  "email": "test@gmail.com",
  "first_name": "Claude",
  "last_name": "Test",
  "group_name": "LAS.enduser",
  "mobile_country_code": "91",
  "mobile_number": "9000000903",
  "password": "<password>",
  "two_factor_code_type": "OTP",
  "two_factor_delivery_channel": "SMS"
}
```

## Shared

### 5. Service catalogue
`GET /api/v1/services`. No body. Returns the joining fee and every service.

```json
{
  "joining_fee": 295,
  "services": [
    { "code": "DPR_GENERATION", "name": "DPR Generation", "fee": 2000,
      "description": "…", "external": false, "portal_url": null }
  ]
}
```

### 6. Wallet balance
`GET /api/v1/wallet/balance?wallet_id=97315`. No body.

`wallet_id` is optional and defaults to the signed-in user (a BD's wallet id is
their BD id; a customer's is their customer id). Any other id gets `403`.

```json
{ "wallet_id": "97315", "tag_id": "tag1",
  "total_amount": 1000.0, "available_amount": 1000.0, "locked_amount": 0.0 }
```

A missing amount is `null`, never `0`.

## BD: customers

### 7. List my customers
`GET /api/v1/bd/customers`. No body. Returns `{ broker_id, count, customers: [...] }`.
Each customer has their details, opted `services`, pending `requests` and
the payment and document summary.

### 8. Add a customer
`POST /api/v1/bd/customers`. Returns `201`.

```json
{
  "first_name": "Kunal",
  "last_name": "B",
  "mobile_number": "9000000013",
  "email_address": "kunal@example.com",
  "street_address": "12 MG Road",
  "city": "Bengaluru",
  "state_province": "Karnataka",
  "postal_code": "560001",
  "country_code": "IN",
  "mobile_otp_token": "…",
  "email_otp_token": "…"
}
```

`mobile_otp_token` and `email_otp_token` come from endpoint 34, after
verifying each with purpose `register` and `person_id` = the mobile number.
Without them (while OTP is required) the response is `400
OTP_VERIFICATION_REQUIRED` and nothing is registered.

This runs three steps in order: register the person, open the wallet, create
the login. The mobile number becomes the customer id and login user id.
`last_name` is optional.

```json
{
  "person_id": "9000000013",
  "registration": { "transaction_id": "…", "correlation_id": "…", "timestamp": "…" },
  "wallet_opened": true,
  "wallet": { "transaction_id": "…", "correlation_id": "…", "timestamp": "…" },
  "wallet_error": null,
  "login_created": true,
  "login_user_id": "9000000013",
  "login": { "transaction_id": "…", "correlation_id": "…", "timestamp": "…" },
  "login_error": null,
  "temporary_password": "5Vc…"
}
```

- `temporary_password` is returned **only here and only once**, and is never
  stored. The response is sent with `Cache-Control: no-store`.
- If the wallet or login step fails, the customer is still registered: check
  `wallet_opened` / `login_created` and retry with endpoint 10 or 11.

### 9. One customer
`GET /api/v1/bd/customers/{person_id}`. No body. Same shape as one entry in
endpoint 7.

### 10. Open the wallet (retry)
`POST /api/v1/bd/customers/{person_id}/wallet`. No body. Safe to repeat: a
wallet that already exists returns `already_open: true`.

```json
{ "person_id": "9000000013", "already_open": false, "wallet": { "transaction_id": "…" } }
```

### 11. Create the login (retry)
`POST /api/v1/bd/customers/{person_id}/login`. No body. Uses the name and
email on the customer record, and the server's super-admin account.

```json
{ "person_id": "9000000013", "login_user_id": "9000000013",
  "already_exists": false, "login": { "transaction_id": "…" },
  "temporary_password": "5Vc…" }
```

If a login already exists, `already_exists` is `true` and no password is
returned.

### 12. Change email
`PUT /api/v1/bd/customers/{person_id}/email`

```json
{ "email_address": "test@gmail.com", "email_otp_token": "…" }
```

`email_otp_token` is for the **new** email (endpoint 34, purpose `update`,
`person_id` = this customer). Without it: `400 OTP_VERIFICATION_REQUIRED`.

```json
{ "person_id": "9000000903",
  "updated": { "transaction_id": "7521111470108205056",
               "correlation_id": "54816ccf-…", "timestamp": "2026-10-28T07:31:49Z" } }
```

This changes the customer record only; the customer's login keeps its
original email.

### 13. Change address
`PUT /api/v1/bd/customers/{person_id}/address`. Send the whole address: the
vendor replaces it.

```json
{
  "street_address": "12 MG Road",
  "city": "Bengaluru",
  "state_province": "Karnataka",
  "postal_code": "560001",
  "country_code": "IN"
}
```

Response: same shape as endpoint 12.

## BD: services, payment and documents

### 14. A customer's services
`GET /api/v1/bd/customers/{person_id}/services`. No body.

```json
{
  "person_id": "9000000903",
  "services": [
    {
      "code": "DPR_GENERATION", "name": "DPR Generation", "fee": 2000,
      "opted": true, "requested": false,
      "status": "Opted", "payment_status": "Paid", "payment_mode": "Pay later",
      "transaction_id": "7521076925685760000",
      "document": null, "can_upload": true
    }
  ],
  "payment_status": "Paid", "payment_mode": "Mixed", "doc_status": "Partially uploaded",
  "first_payment": false, "payment_in_progress": false
}
```

- **`status`** (service lifecycle): `NA` → `Awaiting BD` → `Opted` →
  `Docs uploaded` → `Approved` / `Rejected` → `Report ready`.
- **`requested: true`:** the customer asked for this service and the BD has
  not yet acted.
- **`first_payment: true`:** the next payment includes the ₹295 joining fee.

### 15. Opt in and pay
`POST /api/v1/bd/customers/{person_id}/payment`

Pay now (dummy QR; no money moves, the BD confirms receipt):

```json
{ "services": ["DPR_ANALYSIS", "GEO_CHECK"], "mode": "pay_now",
  "details": { "DPR_ANALYSIS": { "bank_name": "SBI", "project_name": "Dairy unit" } } }
```

`details`: what the chosen services ask for (`details` in endpoint 5). DPR
Analysis needs `bank_name` and `project_name` (each up to 100 characters).
Missing or extra details are a `422`. They are saved first, before anything
is opted or charged; a customer registered before these fields existed gets
`400 CUSTOM_FIELD_ID_NOT_EXISTS` and nothing else happens. Endpoint 14 returns
them per service as `details: [{key, label, value}]`.

Pay later (both consents required; **moves money** from the pool to `IntegraMain`):

```json
{
  "services": ["GEO_CHECK"],
  "mode": "pay_later",
  "bd_consent": true,
  "customer_consent": true
}
```

```json
{
  "person_id": "9000000902",
  "mode": "pay_later",
  "services": ["GEO_CHECK"],
  "amount": 795,
  "joining_fee": 295,
  "payment_status": "Paid",
  "transfer": { "transaction_id": "7520906027847049216", "correlation_id": "…", "timestamp": "…" },
  "status_recorded": true
}
```

- **`status_recorded: false`:** the money moved but the record wasn't updated.
  **Never pay again**; contact support with the transaction id.
- **`502 PAYMENT_OUTCOME_UNKNOWN`:** the transfer may or may not have happened.
  Payments for this customer are blocked until it is reconciled.

### 16. Authorize a customer's request
`POST /api/v1/bd/customers/{person_id}/services/{code}/authorize`

For a Pay later request, the BD stands guarantor and the pool transfer runs
now (**moves money**):

```json
{ "bd_consent": true }
```

For a Pay now request, the BD confirms the payment arrived. Send `{}`.
Response: same as endpoint 15.

### 17. Decline a customer's request
`POST /api/v1/bd/customers/{person_id}/services/{code}/decline`. No body.
Nothing is charged; the service goes back to not opted.

### 18. Upload a service document
`PUT /api/v1/bd/customers/{person_id}/services/{code}/document?filename=project-report.pdf`

**The body is the raw file, not JSON**, with its own `Content-Type`
(`application/pdf`, `image/jpeg` or `image/png`), and at most 2 MB.

```bash
curl -X PUT "http://localhost:8000/api/v1/bd/customers/9000000903/services/DPR_ANALYSIS/document?filename=project-report.pdf" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/pdf" \
  --data-binary @project-report.pdf
```

```json
{
  "person_id": "9000000903",
  "code": "DPR_ANALYSIS",
  "document": { "name": "project-report.pdf", "content_type": "application/pdf", "size_bytes": 182344 },
  "doc_status": "Partially uploaded"
}
```

- **When it's allowed:** only once the service is paid, and only until it is
  approved (`Approved` or `Report ready` lock it).
- **Replacing:** a new upload replaces the earlier document.

### 19. Download a service document
`GET /api/v1/bd/customers/{person_id}/services/{code}/document`. No body.
Returns the file itself, with `Content-Disposition: attachment`.

## Customer (signed in as the customer)

The customer id always comes from the token; these routes take no
`person_id`.

### 20. My profile
`GET /api/v1/customer/profile`. No body.

```json
{
  "person_id": "9000000903",
  "first_name": "Claude", "last_name": "Test 3",
  "mobile_number": "9000000903", "mobile_country_code": "91",
  "email_address": "test@gmail.com",
  "address": { "street_address": "…", "city": "Bengaluru", "state_province": "Karnataka",
               "postal_code": "560001", "country_code": "IN" },
  "broker_id": "97315"
}
```

### 21. My services
`GET /api/v1/customer/services`. No body. Same shape as endpoint 14.

### 22. Request services
`POST /api/v1/customer/services/request`

Pay later (the customer promises to pay; nothing is charged until the BD
authorizes):

```json
{ "services": ["GEO_CHECK"], "mode": "pay_later", "customer_consent": true }
```

`details` works as in endpoint 15 (DPR Analysis needs the bank and project).

Pay now (while the QR is a placeholder, the BD confirms receipt):

```json
{ "services": ["GEO_CHECK"], "mode": "pay_now" }
```

```json
{ "person_id": "9000000903", "mode": "pay_later", "services": ["GEO_CHECK"],
  "amount": 500, "awaiting_bd": true, "payment": null }
```

### 23. Upload my document
`PUT /api/v1/customer/services/{code}/document?filename=site-photo.jpg`. Raw
file body, same rules as endpoint 18.

### 24. Download my document
`GET /api/v1/customer/services/{code}/document`. No body. Returns the file.

## Low-level (admin and support)

### 25. Register a person
`POST /api/v1/persons`. The dashboard uses endpoint 8 instead, which fills in
everything below from config.

```json
{
  "person_id": "9000000013",
  "first_name": "Kunal",
  "last_name": "B",
  "email_address": "kunal@example.com",
  "address_street_address": "12 MG Road",
  "address_city": "Bengaluru",
  "address_state_province": "Karnataka",
  "address_postal_code": "560001",
  "address_country_code": "IN",
  "mobile_number": "9000000013",
  "mobile_country_code": "91",
  "settlement_account_no": "<account number>",
  "settlement_account_ifsc_code": "<IFSC>",
  "group_id": "Merchant",
  "broker_id": "97315",
  "custom_fields": { "GeoCheck": "NA" }
}
```

### 26. Persons by broker
`GET /api/v1/persons?broker_id=97315`. No body.

### 27. One person
`GET /api/v1/persons/{person_id}`. No body.

### 28. Create a custom field definition
`POST /api/v1/custom-fields`. Changes the program manager's setup for every
customer; use with care.

```json
{
  "custom_field_id": "GeoCheck_Payment",
  "custom_field_name": "GeoCheck_Payment",
  "custom_field_description": "Geo Check payment status",
  "custom_field_data_type": "Text",
  "is_searchable": false,
  "is_unique": false,
  "search_scope": "NA"
}
```

### 29. List custom field definitions
`GET /api/v1/custom-fields`. No body.

### 30. Set one custom field value
`PUT /api/v1/persons/{person_id}/custom-fields/{custom_field_id}`

```json
{ "custom_field_value": "Opted" }
```

### 31. Set several custom field values
`PUT /api/v1/persons/{person_id}/custom-fields`. All or nothing.

```json
{ "custom_fields": { "GeoCheck": "Opted", "GeoCheck_Payment": "Paid" } }
```

## Known limits

- **Name and mobile number can't be edited.** There is no vendor update API
  for them yet.
- **Custom fields added later don't reach older customers.** A customer only
  has the custom fields that existed when they were registered; updating a
  newer one returns `400 CUSTOM_FIELD_ID_NOT_EXISTS` until the vendor fixes
  this.
- **Documents are stored on the customer record.** Every customer-list call
  returns them, so they should move to object storage before many customers
  have documents.

## OTP verification

Verifies a customer's mobile number (SMS) or email before it is saved: when
a BD adds a customer (both) and when an email is changed. Send, then verify;
the verify response's `verification_token` goes with the save. Tokens are
bound to the exact value, purpose, customer and signed-in user, and expire
(session 10 min, proof 30 min).

### 32. OTP settings
`GET /api/v1/otp/settings`. No body.

```json
{ "required": true, "code_length": 6, "max_attempts": 3, "resend_after_seconds": 30 }
```

`required: false` (local runs only) means saves need no token.

### 33. Send an OTP
`POST /api/v1/otp/send`

```json
{ "channel": "mobile", "value": "9000000013", "purpose": "register", "person_id": "9000000013" }
```

- `channel`: `mobile` (SMS) or `email`.
- `purpose`: `register` (BD only; `person_id` = the mobile being registered,
  and for `mobile` the value must be that same number) or `update` (the
  customer's own BD, or the customer themselves; `person_id` = the customer).

```json
{ "otp_session": "…", "sent_to": "******0013", "expires_at": 1793785200,
  "resend_after_seconds": 30, "max_attempts": 3, "code_length": 6 }
```

Errors: `409 CUSTOMER_ALREADY_REGISTERED` (register, mobile exists — no OTP is
sent), `404 CUSTOMER_NOT_FOUND` (update, not your customer), `429
OTP_RESEND_TOO_SOON`, `422 OTP_INVALID_MOBILE` / `OTP_INVALID_EMAIL` /
`OTP_VALUE_MISMATCH`, `502 OTP_SEND_FAILED`, `503 OTP_NOT_CONFIGURED`.

### 34. Verify an OTP
`POST /api/v1/otp/verify`

```json
{ "otp_session": "…", "otp": "861283" }
```

```json
{ "verified": true, "channel": "mobile", "value": "9000000013",
  "verification_token": "…", "expires_at": 1793786400 }
```

Errors (all `400` unless noted): `OTP_INVALID` (message says how many
attempts are left), `OTP_ATTEMPTS_EXCEEDED` (after 3 wrong codes — send a new
OTP), `OTP_SESSION_EXPIRED`, `OTP_ALREADY_USED`, `502`/`504
OTP_VERIFY_FAILED` (the service failed; no attempt is used).
