from orchestrators.defs.shared.partitions import queue_items_partition_def

PIPELINE_TAG = "extract-queued-items"

SENSOR_MIN_INTERVAL_S = 900
MAX_QUEUED_PER_TICK = 5
FETCHED_CONTENT_MIN_CHARS = 2000

LIFECYCLE_DRIFT_AGE_MINUTES = 60

JOB_MAX_RETRIES = "1"

__all__ = [
    "FETCHED_CONTENT_MIN_CHARS",
    "JOB_MAX_RETRIES",
    "LIFECYCLE_DRIFT_AGE_MINUTES",
    "MAX_QUEUED_PER_TICK",
    "PIPELINE_TAG",
    "SENSOR_MIN_INTERVAL_S",
    "queue_items_partition_def",
]
