# Increment 4 — Decoupled report readiness from Wallet storage

## Changed
- Added `report_access.py`: pure policy to distinguish report generation from report release. Wallet upload success is **not** an unconditional report-release requirement. Report release requires current trusted payment verification, ownership, consent, source identity, verified report artifact and an independently approved document-retention status.
- Added `branch_status.py` and migration `002_dpr_branch_status.sql`: independent Wallet retry/storage status and report creation status for each analysis run. No payment authority duplicated in this table.
- Existing strict `can_finalize` in `upload_fanout.py` is retained *only for backward compatibility*, explicitly disallowed as report release authorization.
- Added policy regression tests including Wallet failure + successful analysis, unpaid Pay Later, unknown retention, unauthorized access, and stored-wallet/unpaid cases.

## Integration outstanding (not production-ready)
- Call `init_branches` after inserting a run, update wallet branch independently, retry Wallet uploads through durable jobs, and complete the report branch with an approved artifact writer.
- Connect report download to authenticated Core LAS authorization service and **fresh Wallet payment verification**; this module does not fetch the payment API itself.
- Decide and enforce the source-document retention policy; temporary staging is not assumed to be compliant.
- Run migrations on Core LAS's actual PostgreSQL database, run integration tests for concurrent branch races, and ensure tenant/case ownership in all APIs.
- The source's current Wallet custom-field mechanism does not return a dedicated document ID: preserve its real authoritative reference, do not invent one.
