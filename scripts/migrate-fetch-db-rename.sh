#!/usr/bin/env bash
# One-shot migration for the fetcher DB rename:
#   table cache   -> fetch_cache
#   table fetches -> async_jobs (+ job_type column)
#   file  fetches.db -> fetch.db
#
# Run on the host after the rename lands and BEFORE deploying the new image:
# the service's create_schema() is CREATE TABLE IF NOT EXISTS, so a container
# that boots on the old file creates empty new-name tables beside the populated
# old-name ones and strands every cached row.
#
# Safe to re-run: exits 0 without touching anything if already migrated.
set -euo pipefail

DATA_DIR="${DATA_DIR:-/home/deploy/knowledge-pipeline/data}"
OLD="$DATA_DIR/fetches.db"
NEW="$DATA_DIR/fetch.db"

if [[ -f "$NEW" && ! -f "$OLD" ]]; then
  echo "already migrated: $NEW exists and $OLD does not"
  exit 0
fi
[[ -f "$OLD" ]] || { echo "FATAL: $OLD not found"; exit 1; }

if docker compose ps --status running --services 2>/dev/null | grep -qx fetcher; then
  echo "FATAL: the fetcher is running — stop it first:"
  echo "  docker compose stop fetcher"
  exit 1
fi

# Checkpoint before the snapshot: in WAL mode the newest committed pages can
# still live in fetches.db-wal, and a copy of the main file alone would miss
# them. .backup is WAL-aware; cp is not.
sqlite3 "$OLD" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null
SNAP="$OLD.pre-rename-$(date -u +%Y%m%dT%H%M%SZ)"
sqlite3 "$OLD" ".backup '$SNAP'"
echo "snapshot: $SNAP"

# One transaction, so a mid-script failure cannot leave a half-renamed schema.
#
# The DROP INDEX lines are required, not tidying: SQLite retargets an index at
# the renamed table but keeps the index's OLD name, so the service's
# `CREATE INDEX IF NOT EXISTS fetch_cache_expires_at` would build a SECOND index
# over the same column and leave the old-named one behind forever.
#
# journal_mode=DELETE folds every page into the main file and removes the -wal
# and -shm sidecars, so the file rename below carries the whole database. The
# service switches the DB back to WAL on its next boot.
sqlite3 "$OLD" <<'SQL'
BEGIN IMMEDIATE;
ALTER TABLE cache   RENAME TO fetch_cache;
ALTER TABLE fetches RENAME TO async_jobs;
ALTER TABLE async_jobs ADD COLUMN job_type TEXT NOT NULL DEFAULT 'fetch';
DROP INDEX IF EXISTS cache_expires_at;
DROP INDEX IF EXISTS fetches_status;
DROP INDEX IF EXISTS fetches_batch_id;
DROP INDEX IF EXISTS fetches_expires_at;
COMMIT;
PRAGMA wal_checkpoint(TRUNCATE);
PRAGMA journal_mode=DELETE;
SQL

mv "$OLD" "$NEW"
rm -f "$OLD-wal" "$OLD-shm"

echo "--- verification ---"
sqlite3 "$NEW" "PRAGMA integrity_check;"
sqlite3 "$NEW" "SELECT 'fetch_cache', count(*) FROM fetch_cache
                UNION ALL SELECT 'async_jobs', count(*) FROM async_jobs
                UNION ALL SELECT 'url_aliases', count(*) FROM url_aliases;"
sqlite3 "$NEW" "PRAGMA table_info(async_jobs);" | grep -q job_type \
  || { echo "FATAL: job_type column missing"; exit 1; }
echo "migration OK — restore with: cp '$SNAP' '$OLD'"
