# Increment 3 — Parallel LangGraph + durable job primitives

## Implemented in Core LAS
- `parallel_graph.py`: LangGraph planner-driven dependency DAG: document → planner → (finance, sector, research, policy concurrently) → risk → verifier → correction → report. Per-branch results are merged with an associative dictionary reducer. Roles remain the existing Gemini prompts.
- `upload_fanout.py`: independent concurrent Wallet and AI branch execution adapter, with fail-closed release gate. The *actual* upload endpoint has not yet been wired to these branches.
- `jobs.py`: PostgreSQL-backed idempotent analysis-run registration, lease-based claiming, ownership-checked completion and status queries.
- `worker.py`: integration seam for externally supplied Wallet document loader, artifact saver, and optional LangGraph checkpointer.
- `migrations/001_dpr_analysis_runs.sql`: proposed additive **Core LAS** migration; no Wallet or LAS_Email schema mutation.
- `tests/test_dpr_parallel_graph.py`: three targeted regression tests (requires missing dependencies in execution environment).

## Deploy prerequisites / incomplete production integrations
1. Install declared dependencies and regenerate existing hash-locked `requirements.txt` and `requirements-dev.txt` using the project's lock tool. No packages were successfully installed here due to blocked package access.
2. Provision a **Core LAS-owned** PostgreSQL database and apply reviewed migration. Call `AsyncPostgresSaver.setup()` (from langgraph checkpoint postgres) during deployment setup if the checkpointing feature is selected. Supply the checkpointer from the actual worker lifecycle; it is not instantiated in this increment.
3. Implement a durable intake outbox that persists stage references and enqueues **both** tasks after one committed transaction; `asyncio.gather` is a two-branch execution helper, not a durable queue. Do not run long-lived tasks directly inside the HTTP process.
4. Bind the worker's `load_input` to authenticated Wallet document retrieval and existing LiteParse/Gemini parsing. `store_result` must put artifacts under Core LAS ownership, with authorization and payment gating. Document bytes must not be persisted as plaintext in the job table.
5. Confirm the real Wallet authoritative document reference. Existing custom-field storage does not demonstrate a dedicated Wallet document ID.
6. Add job lease renewal or bounded execution shorter than lease, recovery sweeper, failure classification and exponential retries, per-tenant limits, model token budgets, and distributed trace IDs.
7. Wire the real upload coordinator, job status API, report lock and dashboard only after the Wallet contract and authorization flow are verified.
8. Add controlled numerical execution, policy provenance, OCR fallback, and independently annotated DPR evaluations before production approval.

## Concurrency and trust
- Four analytical branches execute concurrently; remaining nodes wait for their prerequisites. All model results remain untrusted until source quote validation and independent financial recomputation.
- Research and policy roles currently lack authenticated external search/corpus access. They must not claim current policy compliance.
- Agentic analysis never sanctions a loan or unlocks a report; those operations belong to authorized Core LAS workflows.
- Existing `/analyze` and `/inspect-upload` behavior is untouched and feature-gated.

## Local verification
- `compileall` succeeds for the added modules.
- Full pytest cannot start: existing `tests/conftest.py` imports unavailable `respx`.
- LangGraph/PostgreSQL execution not run: `langgraph`/`psycopg` are unavailable, and pip access fails due to network resolution.
- No Wallet, production Gemini, or approved/rejected DPR end-to-end execution claimed.
