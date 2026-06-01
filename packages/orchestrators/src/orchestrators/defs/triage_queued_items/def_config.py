"""Constants for the triage_queued_items pipeline."""

from orchestrators.defs.shared.partitions import queue_items_partition_def

PIPELINE_TAG = "triage-queued-items"

SENSOR_MIN_INTERVAL_S = 900
MAX_QUEUED_PER_TICK = 10  # higher than extract — triage is cheap
TITLE_FETCH_TIMEOUT_S = 10

JOB_MAX_RETRIES = "1"

__all__ = [
    "JOB_MAX_RETRIES",
    "MAX_QUEUED_PER_TICK",
    "PIPELINE_TAG",
    "SENSOR_MIN_INTERVAL_S",
    "TITLE_FETCH_TIMEOUT_S",
    "queue_items_partition_def",
]
