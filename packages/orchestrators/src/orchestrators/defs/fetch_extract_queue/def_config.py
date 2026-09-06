from orchestrators.defs.shared.partitions import queue_items_partition_def

PIPELINE_TAG = "extract-complex-contents"

SENSOR_MIN_INTERVAL_S = 900
MAX_TO_EXTRACT_PER_TICK = (
    2  # renamed from MAX_QUEUED_PER_TICK; lower than triage's because extraction is expensive
)

LIFECYCLE_DRIFT_AGE_MINUTES = 60

JOB_MAX_RETRIES = "1"

# Content Type values that this pipeline supports. Triage routes every row
# whose type is in this set to Status=Fetching; the fetcher service's handler
# registry then claims the URL. Every type has a handler (article is the
# catch-all, so `other` reaches it safely), so this is the full taxonomy —
# must match the Notion "Content Type" SELECT options + `classify.ALL_CONTENT_TYPES`.
SUPPORTED_CONTENT_TYPES: tuple[str, ...] = (
    "youtube",
    "arxiv",
    "medium",
    "facebook",
    "github",
    "file_pdf",
    "file_audio",
    "article",
    "other",
)

# Which prompt each extraction task runs is no longer declared here. The fetcher
# service owns extraction, so it owns the labels too — see its `extract/tasks.py`.
# Naming them in both places would let this repo record a version the service
# never ran.

__all__ = [
    "JOB_MAX_RETRIES",
    "LIFECYCLE_DRIFT_AGE_MINUTES",
    "MAX_TO_EXTRACT_PER_TICK",
    "PIPELINE_TAG",
    "SENSOR_MIN_INTERVAL_S",
    "SUPPORTED_CONTENT_TYPES",
    "queue_items_partition_def",
]
