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

# Active prompt labels — basenames of the markdown files under prompts/.
# Code-level constants (not env vars) because prompt versions don't vary
# per-deployment: dev and prod ship the same prompt until somebody bumps
# the version here. Same pattern as the `_DAG_VERSION` constants in
# orchestrators/config.py — manual bump on a prompt-shape change.
PROMPT_LABEL_NARRATIVE = "narrative_v1"
PROMPT_LABEL_TOPIC_CARD = "topic_card_v1"
PROMPT_LABEL_FOLLOWUPS = "followups_v1"

__all__ = [
    "FETCHED_CONTENT_MIN_CHARS",
    "JOB_MAX_RETRIES",
    "LIFECYCLE_DRIFT_AGE_MINUTES",
    "MAX_TO_EXTRACT_PER_TICK",
    "PIPELINE_TAG",
    "PROMPT_LABEL_FOLLOWUPS",
    "PROMPT_LABEL_NARRATIVE",
    "PROMPT_LABEL_TOPIC_CARD",
    "SENSOR_MIN_INTERVAL_S",
    "SUPPORTED_CONTENT_TYPES",
    "queue_items_partition_def",
]
