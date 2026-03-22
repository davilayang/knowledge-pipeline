# Knowledge Pipeline

Content indexing and backup pipeline for the newsletter-assistant knowledge system, 
using Dagster, SQLite, and ChromaDB.

## Prerequisites

- Python >= 3.13
- Docker & Docker Compose
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
# Install dependencies
uv sync
```

### Local Development

Start the Dagster dev server (includes webserver + daemon):

```bash
uv run poe dev
```

- Dagster UI: http://localhost:3000
- Stop with `Ctrl-C`

### Dagster Cluster (Docker)

For a persistent deployment with PostgreSQL-backed storage:

```bash
# Set up environment
cp .env.example .env
# Edit .env if needed

# Start cluster (Postgres, code server, webserver, daemon)
docker compose up

# After code changes, restart the code server (webserver/daemon stay up)
docker compose restart dagster-code

# Tear down
docker compose down --volumes
```

- Dagster UI: http://localhost:3000

## Running Jobs

**Via Dagster UI** (recommended): http://localhost:3000 → Assets → Materialize or Jobs → Launch Run

**Via CLI** (one-shot execution):

```bash
# Index pending content into ChromaDB (copies raw_store.db first)
uv run poe index

# Backup databases from newsletter-assistant
uv run poe backup
```

### Index Contents Job

Copies `raw_store.db` from the newsletter-assistant project, chunks pending content using markdown-aware splitting, embeds into ChromaDB, and updates vector status.

Asset chain: `raw_store_copy` → `pending_contents` → `indexed_contents`

### Backup Job

Copies SQLite databases from newsletter-assistant to timestamped backup directories under `backups/`, with automatic retention cleanup (default: 7 backups).

## Architecture

```
src/knowledge_pipeline/
  definitions.py          # Merges all defs/ into a single Definitions object
  defs/                   # Dagster pipeline code (each subfolder exports its own Definitions)
    index_contents/
      __init__.py         # Job, schedule, and resource registration
      assets.py           # raw_store_copy → pending_contents → indexed_contents
      resources.py        # RawStoreResource, VectorStoreResource
    backup_databases/
      __init__.py         # Job and resource registration
      ops.py              # backup_databases → cleanup_old_backups → log_summary
      resources.py        # BackupResource
  lib/                    # Plain Python, no Dagster
    config.py             # Paths, settings (source project, local data, backup retention)
    store.py              # Read-only SQLite access to raw_store.db
    chunking.py           # Markdown-aware chunking (heading boundaries, paragraph packing)
    vector_store.py       # ChromaDB operations (embed, search)
```

**Data flow:** `~/GitHub/newsletter-assistant/data/raw_store.db` → copy → `data/raw_store.db` → chunk → `data/chroma/`

## References

- [Dagster documentation](https://docs.dagster.io/)
- [Dagster project structure guide](https://docs.dagster.io/guides/build/projects/project-structure/organizing-dagster-projects)
