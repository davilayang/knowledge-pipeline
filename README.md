# Knowledge Pipeline

Dagster-orchestrated pipelines for the [newsletter-assistant](https://github.com/davilayang/newsletter-assistant) knowledge system: indexing article content into ChromaDB, synthesising entity wiki pages from raw articles, evaluating RAG strategies, and backing up the source SQLite databases.

## Project Structure

```
packages/
  domains/         # Pure data layer — types, schema, sources (no LLM/Dagster deps)
  workflows/       # LangGraph workflows + agent primitives (wiki synthesis, ingest)
  retrievers/      # RAG infrastructure — chunking, embedding, vector store
  evals/           # Eval harnesses — RAG metrics, wiki quality dimensions
  orchestrators/   # Dagster definitions (the only package allowed to import Dagster)
data/              # Runtime — gitignored (raw_store.db, chroma/, wiki/)
backups/           # Local snapshot landing — gitignored
datasets/          # Pinned eval datasets — checked in
docker/            # Per-service Dockerfiles (postgres init, dagster-code, dagster)
scripts/           # Deployment scripts (deploy-hcloud.sh)
```

## Development Instructions

**Prerequisites**

1. **Python 3.13** and [`uv`](https://docs.astral.sh/uv/)
2. **Docker & Docker Compose** (for the Postgres + Dagster cluster)
3. **API keys** — copy `.env.example` to `.env` and fill in
   - `OPENAI_API_KEY` — wiki synthesis LLM
   - `DAGSTER_PG_*` — Postgres credentials for Dagster metadata DB

```bash
uv sync

uv run poe check       # fmt, lint, tests
uv run poe test
uv run poe fix         # auto-fix formatting (black) + linting (ruff)
uv run pytest tests/path/to/test_file.py -v   # single test file
```

### Local Development

```bash
uv run poe dev
```

- Dagster UI: http://localhost:3030 (no path prefix in `poe dev`)
- Stop with `Ctrl-C`. Dev mode runs the webserver and daemon in one process; sensors and schedules tick automatically.

### Dagster Cluster (Docker)

For a persistent deployment with PostgreSQL-backed storage:

```bash
docker compose up

# After code changes, rebuild + restart the code server (webserver/daemon stay up)
docker compose restart dagster-code

# Tear down
docker compose down --volumes
```

- Dagster UI: http://localhost:3030/dags (the Docker webserver runs with `--path-prefix=/dags` for reverse-proxy mounting)

## Running Jobs

**Via Dagster UI** (recommended): http://localhost:3030 (`poe dev`) or http://localhost:3030/dags (Docker) → Assets → Materialize, or Jobs → Launch Run

**Via CLI** (one-shot):

```bash
uv run poe index            # Index pending content into ChromaDB
uv run poe backup           # Snapshot newsletter-assistant SQLite DBs
uv run poe eval             # Evaluate RAG strategies
uv run poe wiki-ingest      # Run wiki synthesis (LangGraph workflow)
uv run poe wiki-eval        # Score wiki pages across quality dimensions
uv run poe rag-eval         # Run RAG evaluation harness with Ragas metrics
```

### Pipelines

- **`backup_readings`** — daily-partitioned snapshot of newsletter-assistant SQLite DBs, with optional Drive offload via `rclone` and a healthchecks.io ping. See [`packages/orchestrators/src/orchestrators/defs/backup_readings/README.md`](packages/orchestrators/src/orchestrators/defs/backup_readings/README.md) for env vars, rclone setup, and runbook.

- **`idx_*`** — four index strategies (markdown × MiniLM/BGE, recursive × MiniLM, semantic × MiniLM). Run them to compare chunking/embedding combinations against the eval dataset.

- **`wiki_synthesized`** — LangGraph-checkpointed wiki synthesis from raw articles to entity wiki pages, with per-entity Send-API fan-out and transactional Postgres commits.

## Deployment (Hetzner Cloud)

```bash
./scripts/deploy-hcloud.sh setup           # one-time provisioning
./scripts/deploy-hcloud.sh deploy          # pull latest, rebuild, restart
./scripts/deploy-hcloud.sh push-creds      # sync ~/.config/rclone/rclone.conf to server
```

Run from the repo root. Configure target via `.env.deploy` (copy from `.env.deploy.example`). See `scripts/deploy-hcloud.sh` header for the full flag list.

## Others

### SSH Tunnel

```bash
uv run poe tunnel dagster   # forwards localhost:3030 → server's Dagster UI
```

### Resets

```bash
uv run poe reset-indices       # drop chunks/, embeddings/, chroma/
uv run poe reset-wiki          # drop wiki schema + data/wiki/ + checkpoints
uv run poe reset-checkpoints   # drop langgraph_checkpoints schema only
uv run poe reset-everything    # full nuke
```

## References

- [Dagster docs](https://docs.dagster.io/)
- [Dagster project structure guide](https://docs.dagster.io/guides/build/projects/project-structure/organizing-dagster-projects)
- [LangGraph docs](https://langchain-ai.github.io/langgraph/)
- Companion repo: [newsletter-assistant](https://github.com/davilayang/newsletter-assistant)
