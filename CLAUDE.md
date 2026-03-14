# CLAUDE.md

## Project Overview

Prefect-based orchestrator pipeline for the newsletter-assistant knowledge system. Self-contained project that:
1. **Indexes** content from `raw_store.db` (copied from `~/GitHub/newsletter-assistant`) into a local ChromaDB vector store
2. **Backs up** SQLite databases from newsletter-assistant with timestamped snapshots and retention

## Package Manager

Uses [`uv`](https://docs.astral.sh/uv/). Do not use `pip` or `poetry`.

```bash
uv sync
uv run python -m knowledge_pipeline <command>
```

## Commands

```bash
# Index content into ChromaDB (copies raw_store.db first)
uv run python -m knowledge_pipeline index-knowledge
uv run python -m knowledge_pipeline index-knowledge --source medium --since 2026-03-01
uv run python -m knowledge_pipeline index-knowledge --skip-copy  # use existing local DB

# Backup databases
uv run python -m knowledge_pipeline backup
uv run python -m knowledge_pipeline backup --max-backups 14
```

## With Prefect Server

```bash
docker compose up -d                    # Start Prefect server at http://localhost:4200
PREFECT_API_URL=http://localhost:4200/api uv run python -m knowledge_pipeline index-knowledge
```

## Architecture

```
src/knowledge_pipeline/
  config.py           # Paths, settings (source project, local data, backup retention)
  store.py            # Read-only SQLite access to raw_store.db
  chunking.py         # Markdown-aware chunking (heading boundaries, paragraph packing)
  vector_store.py     # ChromaDB operations (embed, search)
  flows/
    index_knowledge.py  # copy DB → fetch pending → chunk → embed → upsert → report
    backup.py           # copy DBs → cleanup old → report
```

**Data flow:** `newsletter-assistant/data/raw_store.db` → copy → `data/raw_store.db` → chunk → `data/chroma/`

**Source project:** `~/GitHub/newsletter-assistant` (configured in `config.py`)
