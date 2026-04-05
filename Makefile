# --- Dagster dev ---

.PHONY: dev index backup eval build up down logs

dev:  ## Launch Dagster UI at localhost:3000
	uv run poe dev

# --- Helpers ---

.PHONY: help

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | sort | \
	  awk 'BEGIN {FS = ":.*## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
