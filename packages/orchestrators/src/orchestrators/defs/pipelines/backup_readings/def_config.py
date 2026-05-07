# Definition-time config for the readings backup pipeline. Path-level config
# (where to read DBs from, where to land snapshots) lives in orchestrators.config.

import dagster as dg

# ---------- partitioning ----------

# Daily partition start date — anything before this would have nothing to back up.
# The partition_key is an ISO date string (e.g. "2026-05-06") and becomes the
# subdirectory name under BACKUP_DIR and on the Drive remote — single source of
# truth for "which day's snapshot."
PARTITION_START_DATE = "2026-05-01"
daily_partition_def = dg.DailyPartitionsDefinition(start_date=PARTITION_START_DATE)


# ---------- retention ----------

# Local snapshot retention — newest N partition dirs kept under BACKUP_DIR.
# Local is the recent-restore cache; Drive is the long-term archive.
MAX_LOCAL_BACKUPS = 14

# Drive retention — newest N partition dirs kept on the rclone remote.
MAX_DRIVE_BACKUPS = 70


# ---------- Drive ----------

# Capacity preflight — fail the run if used / total exceeds this.
DRIVE_USAGE_THRESHOLD = 0.90


# ---------- snapshot validation ----------

# Asset-check threshold: snapshots smaller than this fail the integrity check.
# An empty SQLite file is ~0–4KB; pick a tiny floor that catches obviously-empty.
MIN_SNAPSHOT_BYTES = 1024


# ---------- scheduling / job tags ----------

# Cron for the daily run. Fires for the previous day's partition (see schedules.py).
SCHEDULE_CRON = "0 3 * * *"

# Dagster job tags. JOB_MAX_RETRIES is a string because Dagster expects strings.
JOB_MAX_RETRIES = "1"

# Single source of truth for the run-group tag on this pipeline.
# Used for both the job's "project" tag (UI filtering) and the snapshot ops'
# concurrency_key (prevent overlapping runs from contending on SQLite read locks).
PIPELINE_TAG = "newsletter-backup"


# ---------- healthcheck ping ----------

# Per-request timeout for the POST to healthchecks.io (see sensors.py).
PING_TIMEOUT_S = 10

# How often the daemon evaluates the run-status sensor. The sensor only fires
# when a new backup_readings SUCCESS event arrives, so checking every 30s
# (Dagster's default) is wasteful for a daily job. 5 min adds at most ~5 min
# lag between job success and the ping — invisible against healthchecks'
# day+hour-scale period+grace window.
SENSOR_MIN_INTERVAL_S = 300
