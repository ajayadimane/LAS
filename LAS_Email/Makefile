.PHONY: run install db-check migrate seed test test-golden test-golden-live lint type fmt worker beat ingest up down

# Python 3.10 virtualenv (this service runs on the same machine as Core LAS, 2026-11-16).
VENV ?= .venv
BIN := $(VENV)/bin

install:
	python3 -m venv $(VENV)
	$(BIN)/pip install -r requirements.lock
	$(BIN)/pip install -e . --no-deps

## Verify the local PostgreSQL server: version >= 14 (prod: 16) and that pgcrypto/citext can be created.
db-check:
	$(BIN)/python -m las.scripts.db_check

migrate:
	$(BIN)/alembic upgrade head

seed:
	$(BIN)/las-seed

test:
	$(BIN)/pytest -q

test-golden:
	$(BIN)/pytest -q tests/golden -s

test-golden-live:
	GOLDEN_LIVE=1 $(BIN)/pytest -q tests/golden -s

lint:
	$(BIN)/ruff check las tests

fmt:
	$(BIN)/ruff format las tests && $(BIN)/ruff check --fix las tests

type:
	$(BIN)/mypy

## Everything in one terminal (local runs): worker with embedded Beat + the mailbox listener.
## Ctrl+C stops both (the listener is force-stopped after 5 s if it is waiting on IMAP IDLE --
## safe: every mail is persisted before processing and deduped on its Message-ID).
## In production use the three systemd units instead (deploy/README.md).
run:
	@mkdir -p var
	@$(BIN)/celery -A las.worker.celery_app worker -B -l info -Q las --concurrency 2 \
		--schedule var/celerybeat-schedule & W=$$!; \
	$(BIN)/las-ingest & I=$$!; \
	trap 'echo "stopping..."; kill -TERM $$W $$I 2>/dev/null; \
		for n in 1 2 3 4 5; do kill -0 $$I 2>/dev/null || break; sleep 1; done; \
		kill -9 $$I 2>/dev/null; wait $$W; exit 0' INT TERM; \
	wait

worker:
	$(BIN)/celery -A las.worker.celery_app worker -l info -Q las

beat:
	$(BIN)/celery -A las.worker.celery_app beat -l info

ingest:
	$(BIN)/las-ingest

up:
	docker compose up -d

down:
	docker compose down
