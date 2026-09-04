SHELL := /bin/bash
PYTHON ?= python3
VENV := .venv
PY := $(VENV)/bin/python
PNPM ?= pnpm

.PHONY: bootstrap install dev dev-api dev-notification-worker dev-candidate-sync-worker dev-data-retention-worker dev-web dev-extension dev-mock-site db-up db-down migrate migration seed test test-api test-web test-extension test-e2e lint typecheck build clean-build generate-api-client boss-cdp

bootstrap: install
	@echo "Bootstrap complete. Copy .env.example to .env before Docker startup."

install:
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install -e 'apps/api[dev]'
	$(PNPM) install

dev:
	docker compose up --build

dev-api:
	PYTHONPATH=apps/api/src $(PY) -m uvicorn recruitment_collab.main:app --reload --port 8000

dev-notification-worker:
	PYTHONPATH=apps/api/src $(PY) -m recruitment_collab.workers.notification_worker

dev-candidate-sync-worker:
	PYTHONPATH=apps/api/src $(PY) -m recruitment_collab.workers.candidate_sync_worker

dev-data-retention-worker:
	PYTHONPATH=apps/api/src $(PY) -m recruitment_collab.workers.data_retention_worker

dev-web:
	$(PNPM) --filter @recruitment/web dev

dev-extension:
	$(PNPM) --filter @recruitment/extension dev

dev-mock-site:
	$(PNPM) --filter @recruitment/mock-site dev

boss-cdp:
	$(PYTHON) scripts/boss_cdp_capture.py --keyword "$(KEYWORD)" --city "$(CITY)" --cdp-port "$(or $(CDP_PORT),9222)" --output "$(or $(OUTPUT),./data/boss/jobs.json)"

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

migrate:
	$(PY) -m alembic -c apps/api/alembic.ini upgrade head

migration:
	$(PY) -m alembic -c apps/api/alembic.ini revision --autogenerate -m "schema update"

seed:
	PYTHONPATH=apps/api/src $(PY) -m recruitment_collab.infrastructure.seed

test: test-api test-web test-extension test-e2e

test-api:
	PYTHONPATH=apps/api/src $(PY) -m pytest apps/api/tests

test-web:
	$(PNPM) --filter @recruitment/web test

test-extension:
	$(PNPM) --filter @recruitment/extension test

test-e2e:
	$(PNPM) --filter @recruitment/extension test:e2e

lint:
	$(PY) -m ruff check apps/api
	$(PNPM) -r lint

typecheck:
	PYTHONPATH=apps/api/src $(PY) -m mypy apps/api/src
	$(PNPM) -r typecheck

generate-api-client:
	PYTHONPATH=apps/api/src $(PY) scripts/generate_openapi.py

build:
	$(PNPM) -r build
	$(PY) -m compileall -q apps/api/src

clean-build:
	find apps packages -type d -name dist -prune -exec rm -r {} +
