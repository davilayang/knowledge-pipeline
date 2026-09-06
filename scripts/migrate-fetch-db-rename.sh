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
# Re-running after a completed migration is a no-op. Re-running after a partial
# one resumes it. Any state it cannot interpret is refused rather than guessed.
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/deploy/knowledge-pipeline}"
DATA_DIR="${DATA_DIR:-$PROJECT_DIR/data}"
OLD="$DATA_DIR/fetches.db"
NEW="$DATA_DIR/fetch.db"

has_table() {  # has_table <db> <name>
  [[ -n "$(sqlite3 "$1" \
    "SELECT name FROM sqlite_master WHERE type='table' AND name='$2';")" ]]
}

if [[ -f "$NEW" && ! -f "$OLD" ]]; then
  echo "already migrated: $NEW exists and $OLD does not"
  exit 0
fi
[[ -f "$OLD" ]] || { echo "FATAL: $OLD not found"; exit 1; }

# Both files existing means the new image booted before this ran and minted an
# empty fetch.db. Renaming over it would destroy whatever it has accumulated
# with no snapshot of it, so stop and let a human decide.
if [[ -f "$NEW" ]]; then
  echo "FATAL: both $OLD and $NEW exist — the service booted before migrating."
  echo "Inspect both, then remove or move $NEW by hand before re-running."
  exit 1
fi

# `docker compose` resolves its project from the working directory, so run it
# from PROJECT_DIR. Failing to ask is treated as "might be running": a rename
# and mv underneath a live fetcher holding the DB open is the one outcome this
# guard exists to prevent.
#
# The check only sees containers of the Compose project in PROJECT_DIR. A
# fetcher started by hand, or under a different project, is invisible to it —
# so this narrows the window, it does not prove exclusivity. Confirm nothing
# else has the DB open before running.
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
# them. .backup is WAL-aware; cp is not.
sqlite3 "$OLD" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null
SNAP="$OLD.pre-rename-$(date -u +%Y%m%dT%H%M%SZ)"
sqlite3 "$OLD" ".backup '$SNAP'"
echo "snapshot: $SNAP"

# Skipped when a previous run crashed between the SQL and the mv below: the
# tables are already renamed, and re-running the ALTERs would abort on the
# missing `cache`.
if has_table "$OLD" cache; then
  # One transaction, so a mid-script failure cannot leave a half-renamed schema.
  #
  # The DROP INDEX lines are required, not tidying: SQLite retargets an index at
  # the renamed table but keeps the index's OLD name, so the service's
  # `CREATE INDEX IF NOT EXISTS fetch_cache_expires_at` would build a SECOND
  # index over the same column and leave the old-named one behind forever.
  #
  # url_aliases is emptied, not carried over: rows written before this release
  # were upserted unconditionally at the 365-day content TTL, so a redirect
  # lookup that failed during any past network blip was recorded as if it had
  # succeeded. Those rows were harmless while only /v1/canonicalize read them;
  # the fetch path reads them now, and the canonical URL is also the content
  # cache key. It is a derived cache — it refills on demand.
  sqlite3 "$OLD" <<'SQL'
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
else
  echo "tables already renamed — resuming at the file rename"
fi

# journal_mode=DELETE folds every page into the main file and removes the -wal
# and -shm sidecars, so the file rename below carries the whole database. The
# service switches the DB back to WAL on its next boot.
sqlite3 "$OLD" "PRAGMA wal_checkpoint(TRUNCATE); PRAGMA journal_mode=DELETE;" >/dev/null
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
