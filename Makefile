# --- Local dev stack ---

.ONESHELL:
SHELL := /bin/bash

# .env is single source of truth for FETCHER_URL. CLI override still wins:
#   FETCHER_URL=http://localhost:8080 make dev
FETCHER_URL ?= $(shell bash -c 'set -a; . .env 2>/dev/null; printf %s "$${FETCHER_URL:-http://localhost:8765}"')
FETCHER_PORT := $(shell echo "$(FETCHER_URL)" | sed -E 's|^.*:([0-9]+).*$$|\1|')

.PHONY: dev dagster-dev fetcher-dev build up down logs tunnel

dev:  ## Start data services + fetcher + Dagster UI (laptop one-shot)
	pkill -f 'dagster|uvicorn fetcher' 2>/dev/null || true
	trap '[ -n "$$FETCHER_PID" ] && kill $$FETCHER_PID 2>/dev/null; docker compose --profile data down' EXIT INT TERM
	docker network inspect kos-network >/dev/null 2>&1 || docker network create kos-network
	docker compose --profile data up -d
	FETCHER_DB_PATH=$(CURDIR)/data/fetches.db uv run --project services/fetcher --env-file .env \
	  uvicorn fetcher.app:app --workers 1 --port $(FETCHER_PORT) &
	FETCHER_PID=$$!
	mkdir -p .dagster_home
	DAGSTER_HOME=$(CURDIR)/.dagster_home uv run --env-file .env dagster dev --port 3030 -m orchestrators.definitions

dagster-dev:  ## Start only Dagster local service
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
