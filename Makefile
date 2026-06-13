# --- Local dev stack ---

.ONESHELL:
SHELL := /bin/bash

FETCHER_PORT ?= 8765

.PHONY: dev dagster-dev fetcher-dev build up down logs tunnel

dev:  ## Start data services + fetcher + Dagster UI (laptop one-shot). Override port with FETCHER_PORT=8765
	pkill -f dagster 2>/dev/null || true
	trap '[ -n "$$FETCHER_PID" ] && kill $$FETCHER_PID 2>/dev/null; docker compose --profile data down' EXIT INT TERM
	docker compose --profile data up -d
	(cd services/fetcher && exec env FETCHER_DB_PATH="$$(pwd)/../../data/fetches.db" \
	  uv run uvicorn fetcher.app:app --workers 1 --port $(FETCHER_PORT) --env-file ../../.env) &
	FETCHER_PID=$$!
	FETCHER_URL=http://localhost:$(FETCHER_PORT) uv run --env-file .env dagster dev --port 3030 -m orchestrators.definitions

dagster-dev:  ## Start only Dagster (when fetcher is already running elsewhere)
	pkill -f dagster 2>/dev/null || true
	uv run poe dagster-dev

fetcher-dev:  ## Start only the fetcher service
	uv run poe fetcher-dev --port $(FETCHER_PORT)

# --- Docker Compose ---

build:  ## Build Docker images
	docker compose build

up:  ## Start Dagster cluster (Postgres, code server, webserver, daemon)
	docker compose up -d --build

down:  ## Stop and remove containers
	docker compose down

logs:  ## Tail logs from all services
	docker compose logs -f

tunnel:  ## SSH tunnel to remote services (dagster or all)
	uv run poe tunnel

# --- Helpers ---

.PHONY: help

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | sort | \
	  awk 'BEGIN {FS = ":.*## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
