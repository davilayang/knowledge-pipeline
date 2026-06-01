from orchestrators.defs.shared.partitions import queue_items_partition_def

PIPELINE_TAG = "extract-complex-contents"

SENSOR_MIN_INTERVAL_S = 900
MAX_TO_EXTRACT_PER_TICK = (
    2  # renamed from MAX_QUEUED_PER_TICK; lower than triage's because extraction is expensive
)
FETCHED_CONTENT_MIN_CHARS = 2000

LIFECYCLE_DRIFT_AGE_MINUTES = 60

JOB_MAX_RETRIES = "1"

# Content Type values that this pipeline supports. Triage routes rows to
# Status=Fetching only when type ∈ this set; rows with unsupported types
# fast-track to Status=Ready at triage time. Extend as new fetcher ports
# land (PDF, Podcast).
SUPPORTED_CONTENT_TYPES: tuple[str, ...] = ("YouTube", "arXiv")

__all__ = [
    "FETCHED_CONTENT_MIN_CHARS",
    "JOB_MAX_RETRIES",
    "LIFECYCLE_DRIFT_AGE_MINUTES",
    "MAX_TO_EXTRACT_PER_TICK",
    "PIPELINE_TAG",
    "SENSOR_MIN_INTERVAL_S",
    "SUPPORTED_CONTENT_TYPES",
    "queue_items_partition_def",
]
