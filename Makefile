# --- Dagster dev ---

.PHONY: dev build up down logs

dev:  ## Launch Dagster UI at localhost:3000
	-pkill -f dagster; uv run poe dev

# --- Docker Compose ---

build:  ## Build Docker images
	docker compose build

up:  ## Start Dagster cluster (Postgres, code server, webserver, daemon)
	docker compose up -d --build

down:  ## Stop and remove containers
	docker compose down

logs:  ## Tail logs from all services
	docker compose logs -f

# --- Helpers ---

.PHONY: help

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | sort | \
	  awk 'BEGIN {FS = ":.*## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
