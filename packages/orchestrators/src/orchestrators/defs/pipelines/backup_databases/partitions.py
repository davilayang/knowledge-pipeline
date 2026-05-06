# Daily partition for the backup pipeline. Each partition_key is an ISO date
# string (e.g. "2026-05-06") and becomes the subdirectory name under BACKUP_DIR
# and the Drive remote — single source of truth for "which day's snapshot."

import dagster as dg

# Start date is when this redesigned pipeline first goes live; backfill before
# this would have nothing to back up against.
daily_partition_def = dg.DailyPartitionsDefinition(start_date="2026-05-01")
