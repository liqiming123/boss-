SHELL := /bin/bash
PYTHON ?= python3
VENV := .venv
PY := $(VENV)/bin/python
PNPM ?= pnpm

.PHONY: bootstrap install dev dev-api dev-web dev-extension dev-mock-site db-up db-down migrate migration seed test test-api test-web test-extension test-e2e lint typecheck build clean-build generate-api-client

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

dev-web:
	$(PNPM) --filter @recruitment/web dev

dev-extension:
	$(PNPM) --filter @recruitment/extension dev

dev-mock-site:
	$(PNPM) --filter @recruitment/mock-site dev

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

migrate:
	cd apps/api && ../../$(PY) -m alembic upgrade head

migration:
	cd apps/api && ../../$(PY) -m alembic revision --autogenerate -m "schema update"

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
