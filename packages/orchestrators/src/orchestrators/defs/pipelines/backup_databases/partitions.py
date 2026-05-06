# Daily partition for the backup pipeline. Each partition_key is an ISO date
# string (e.g. "2026-05-06") and becomes the subdirectory name under BACKUP_DIR
# and the Drive remote — single source of truth for "which day's snapshot."

import dagster as dg

from .def_config import PARTITION_START_DATE

daily_partition_def = dg.DailyPartitionsDefinition(start_date=PARTITION_START_DATE)
