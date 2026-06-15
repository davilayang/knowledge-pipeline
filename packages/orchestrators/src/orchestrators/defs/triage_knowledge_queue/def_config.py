"""Constants for the triage_knowledge_queue pipeline."""

from orchestrators.defs.shared.partitions import queue_items_partition_def

PIPELINE_TAG = "triage-queued-items"

SENSOR_MIN_INTERVAL_S = 900
MAX_QUEUED_PER_TICK = 10  # higher than extract — triage is cheap

JOB_MAX_RETRIES = "1"

# Bump label + add prompts/triage/<new-label>.md in the same commit to roll
# the classifier's system prompt forward. Old file can stay alongside for
# eval comparison.
CONTENT_SHAPE_CLASSIFIER_PROMPT = "content_shape_classifier_v1"

__all__ = [
    "CONTENT_SHAPE_CLASSIFIER_PROMPT",
    "JOB_MAX_RETRIES",
    "MAX_QUEUED_PER_TICK",
    "PIPELINE_TAG",
    "SENSOR_MIN_INTERVAL_S",
    "queue_items_partition_def",
]
