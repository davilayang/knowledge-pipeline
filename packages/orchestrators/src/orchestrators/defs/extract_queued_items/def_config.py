import dagster as dg

PIPELINE_TAG = "extract-queued-items"

SENSOR_MIN_INTERVAL_S = 900
MAX_QUEUED_PER_TICK = 5
FETCHED_CONTENT_MIN_CHARS = 2000

LIFECYCLE_DRIFT_AGE_MINUTES = 60

JOB_MAX_RETRIES = "1"

queue_items_partition_def = dg.DynamicPartitionsDefinition(name="queue_items")
