#!/usr/bin/env bash
# One-shot migration for the fetcher's table renames, in place in fetches.db:
#   cache   -> fetch_cache
#   fetches -> async_jobs (+ job_type column)
#
# Run on the host with the fetcher stopped, after the rename lands and before
# deploying the new image: the service's create_schema() is
# CREATE TABLE IF NOT EXISTS, so a container that boots first creates empty
# new-name tables beside the populated old-name ones and strands every row.
#
# Re-running after a completed migration is a no-op.
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/deploy/knowledge-pipeline}"
DATA_DIR="${DATA_DIR:-$PROJECT_DIR/data}"
DB="$DATA_DIR/fetches.db"

has_table() {  # has_table <name>
  [[ -n "$(sqlite3 "$DB" \
    "SELECT name FROM sqlite_master WHERE type='table' AND name='$1';")" ]]
}

[[ -f "$DB" ]] || { echo "FATAL: $DB not found"; exit 1; }

if ! has_table cache && has_table fetch_cache; then
  echo "already migrated: fetch_cache exists and cache does not"
  exit 0
fi

# An empty fetch_cache beside a populated cache means the new image booted
# before this ran. Merging them is a judgement call, not a script's.
if has_table cache && has_table fetch_cache; then
  echo "FATAL: both cache and fetch_cache exist — the service booted before"
  echo "migrating. Inspect both, drop the empty new-name tables, then re-run:"
  sqlite3 "$DB" "SELECT '  cache='||(SELECT count(*) FROM cache)||
                        '  fetch_cache='||(SELECT count(*) FROM fetch_cache);"
  exit 1
fi

# `docker compose` resolves its project from the working directory, so ask from
# PROJECT_DIR. Not being able to ask is treated as "might be running": rewriting
# the schema underneath a live fetcher is what this guard exists to prevent.
#
# The check only sees containers of the Compose project in PROJECT_DIR. A
# fetcher started by hand, or under another project, is invisible to it — this
# narrows the window, it does not prove exclusivity. Confirm nothing else has
# the database open before running.
if [[ "${SKIP_RUNNING_CHECK:-0}" != "1" ]]; then
  [[ -d "$PROJECT_DIR" ]] || {
    echo "FATAL: PROJECT_DIR=$PROJECT_DIR does not exist, so the running-fetcher"
    echo "check cannot run. Set PROJECT_DIR, or SKIP_RUNNING_CHECK=1 if you have"
    echo "confirmed by hand that nothing has the database open."
    exit 1
  }
  if ! running="$(cd "$PROJECT_DIR" && docker compose ps --status running --services)"; then
    echo "FATAL: could not ask docker compose what is running (in $PROJECT_DIR)."
    echo "Verify the fetcher is stopped, then re-run with PROJECT_DIR set correctly."
    exit 1
  fi
  if grep -qx fetcher <<<"$running"; then
    echo "FATAL: the fetcher is running — stop it first:"
    echo "  cd $PROJECT_DIR && docker compose stop fetcher"
    exit 1
  fi
fi

# Checkpoint before the snapshot: in WAL mode the newest committed pages can
# still live in fetches.db-wal, and a copy of the main file alone would miss
# them. .backup is WAL-aware; cp is not. fetches.db is not covered by the
# backup pipeline, so this snapshot is the only rollback there is.
sqlite3 "$DB" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null
SNAP="$DB.pre-rename-$(date -u +%Y%m%dT%H%M%SZ)"
sqlite3 "$DB" ".backup '$SNAP'"
echo "snapshot: $SNAP"

# One transaction, so a failure part-way cannot leave a half-renamed schema.
#
# The DROP INDEX lines are required, not tidying: SQLite retargets an index at
# the renamed table but keeps the index's OLD name, so the service's
# `CREATE INDEX IF NOT EXISTS fetch_cache_expires_at` would build a SECOND
# index over the same column and leave the old-named one behind forever.
#
# url_aliases is emptied rather than carried over: rows written before this
# release were upserted unconditionally at the 365-day content TTL, so a
# redirect lookup that failed during any past network blip was recorded as if
# it had succeeded. Those rows were harmless while only /v1/canonicalize read
# them; the fetch path reads them now, and the canonical URL is also the
# content cache key. It is a derived cache — it refills on demand.
sqlite3 "$DB" <<'SQL'
BEGIN IMMEDIATE;
ALTER TABLE cache   RENAME TO fetch_cache;
ALTER TABLE fetches RENAME TO async_jobs;
ALTER TABLE async_jobs ADD COLUMN job_type TEXT NOT NULL DEFAULT 'fetch';
DELETE FROM url_aliases;
DROP INDEX IF EXISTS cache_expires_at;
DROP INDEX IF EXISTS fetches_status;
DROP INDEX IF EXISTS fetches_batch_id;
DROP INDEX IF EXISTS fetches_expires_at;
COMMIT;
SQL

echo "--- verification ---"
sqlite3 "$DB" "PRAGMA integrity_check;"
sqlite3 "$DB" "SELECT 'fetch_cache', count(*) FROM fetch_cache
               UNION ALL SELECT 'async_jobs', count(*) FROM async_jobs
               UNION ALL SELECT 'url_aliases', count(*) FROM url_aliases;"
sqlite3 "$DB" "PRAGMA table_info(async_jobs);" | grep -q job_type \
  || { echo "FATAL: job_type column missing"; exit 1; }
echo "migration OK — restore with: cp '$SNAP' '$DB'"
