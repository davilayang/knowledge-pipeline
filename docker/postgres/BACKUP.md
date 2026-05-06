# Postgres backup & restore

One Postgres instance, two databases: `dagster` (Dagster run history) and
`knowledge_pipeline` (the wiki tables and LangGraph checkpoints).

Three files cover a full backup. Per-database dumps mean you can restore
one database without touching the other, and back them up on different
schedules.

## What to back up

| File | Source | Cadence | Why |
|------|--------|---------|-----|
| `globals.sql` | `pg_dumpall --globals-only` | when roles change (rare) | Roles, role passwords, cluster-level grants. Tiny. |
| `knowledge_pipeline-YYYY-MM-DD.sql` | `pg_dump -d knowledge_pipeline` | nightly | Wiki tables + LangGraph checkpoints. Expensive to recreate (LLM cost). |
| `dagster-YYYY-MM-DD.sql` | `pg_dump -d dagster` | weekly | Run history. Regeneratable in principle, but useful for audit. |

## Backup commands

Run from the host (assumes the Postgres container is up and the env vars
are sourced):

```bash
# Globals — only needed if roles or cluster config changed
docker compose exec -T postgres \
  pg_dumpall --globals-only -U "$DAGSTER_PG_USERNAME" \
  > globals.sql

# knowledge_pipeline — nightly
docker compose exec -T postgres \
  pg_dump -U "$DAGSTER_PG_USERNAME" -d knowledge_pipeline \
  > "knowledge_pipeline-$(date +%Y-%m-%d).sql"

# dagster — weekly
docker compose exec -T postgres \
  pg_dump -U "$DAGSTER_PG_USERNAME" -d dagster \
  > "dagster-$(date +%Y-%m-%d).sql"
```

Use `--format=custom` (`-Fc`) instead of plain SQL if size matters or you
want parallel restore — produces a binary file that `pg_restore` can load
with `-j N`.

## Restore — to the existing instance

Wipe and restore one database without touching the other:

```bash
# knowledge_pipeline — drop and reload
docker compose exec -T postgres \
  psql -U "$DAGSTER_PG_USERNAME" -d postgres \
  -c "DROP DATABASE IF EXISTS knowledge_pipeline; CREATE DATABASE knowledge_pipeline;"

docker compose exec -T postgres \
  psql -U "$DAGSTER_PG_USERNAME" -d knowledge_pipeline \
  < knowledge_pipeline-2026-05-06.sql
```

Same shape for `dagster`. The other database is untouched throughout.

## Restore — to a fresh Postgres instance

Three steps in order:

```bash
# 1. Bring up an empty Postgres
docker compose up -d postgres

# 2. Apply globals (creates roles, sets passwords)
docker compose exec -T postgres \
  psql -U postgres -f /path/in/container/globals.sql

# 3. Restore each database
docker compose exec -T postgres \
  createdb -U "$DAGSTER_PG_USERNAME" knowledge_pipeline
docker compose exec -T postgres \
  psql -U "$DAGSTER_PG_USERNAME" -d knowledge_pipeline \
  < knowledge_pipeline-2026-05-06.sql

docker compose exec -T postgres \
  createdb -U "$DAGSTER_PG_USERNAME" dagster
docker compose exec -T postgres \
  psql -U "$DAGSTER_PG_USERNAME" -d dagster \
  < dagster-2026-05-06.sql
```

## What's NOT covered

- **Point-in-time recovery (PITR)**. Requires WAL archiving at the
  instance level, not per-database. Not configured here.
- **`raw_store.db`**. SQLite file at `data/raw_store.db`, separately
  managed by `backup_databases_job` (Dagster).
- **ChromaDB collections** under `data/chroma/`. Re-indexable from raw
  store + strategy configs; not backed up here.
- **`data/wiki/*.md`**. Filesystem markdown pages. They can be
  regenerated from `wiki.pages` rows, but if the .md files are the
  authoritative copy in your workflow, snapshot the directory too.
