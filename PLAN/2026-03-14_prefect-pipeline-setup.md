# Prefect Pipeline for Knowledge Processing & Backup

## Goal
Standalone Prefect-based orchestrator at `~/GitHub/knowledge-pipeline` that:
1. **Indexes** data from `raw_store.db` (copied from newsletter-assistant) into ChromaDB
2. **Backs up** SQLite databases from newsletter-assistant

## Project Structure
```
knowledge-pipeline/
├── pyproject.toml              # uv project with prefect, chromadb deps
├── src/
│   └── knowledge_pipeline/
│       ├── __init__.py
│       ├── __main__.py         # CLI entrypoint
│       ├── config.py           # Paths, settings
│       ├── chunking.py         # Markdown-aware chunking (from newsletter-assistant)
│       ├── store.py            # Read-only SQLite access to raw_store.db
│       ├── vector_store.py     # ChromaDB operations (local copy)
│       └── flows/
│           ├── __init__.py
│           ├── index_knowledge.py   # Index raw_store → ChromaDB
│           └── backup.py            # Copy & backup SQLite DBs
├── data/                       # Local data directory
│   ├── raw_store.db            # Copied from newsletter-assistant
│   └── chroma/                 # ChromaDB index
├── backups/                    # Timestamped DB backups
├── docker-compose.yml          # Prefect server infra
└── CLAUDE.md
```

## Flows

### 1. `index-knowledge` flow
- Copy `raw_store.db` from newsletter-assistant to local `data/`
- Read pending/ready content items from local copy
- Chunk with markdown-aware splitter
- Embed and upsert into local ChromaDB
- Update vector_status in local copy
- Generate Prefect artifacts (summary + table)

### 2. `backup` flow
- Copy all `.db` files from newsletter-assistant/data/ to local `backups/` with timestamp
- Report backup sizes and counts via Prefect artifacts
- Retain configurable number of backups (default: 7)

## Key Decisions
- Self-contained: no workspace dependency on newsletter-assistant packages
- Copy store.py, chunking.py, vector_store.py logic directly (simplified)
- Read-only access to source DBs (copy first, then operate on local copy)
- Uses same ChromaDB embedding function (all-MiniLM-L6-v2)
