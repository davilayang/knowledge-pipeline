from orchestrators.defs.shared.partitions import queue_items_partition_def

PIPELINE_TAG = "extract-complex-contents"

SENSOR_MIN_INTERVAL_S = 900
MAX_TO_EXTRACT_PER_TICK = (
    2  # renamed from MAX_QUEUED_PER_TICK; lower than triage's because extraction is expensive
)

LIFECYCLE_DRIFT_AGE_MINUTES = 60

JOB_MAX_RETRIES = "1"

# Content Type values that this pipeline supports. Triage routes every row
# whose type is in this set to Status=Fetching; the fetcher service's
# handler registry then claims the URL (article handler is a catch-all for
# anything not yt/arxiv/pdf/medium/podcast, so "Article" and "Other" both
# reach it safely). `Podcast` covers audio rows whose URL has no YouTube
# mirror in `podcast_canonicalize.podcast_youtube_map.yaml` — the
# substitution path keeps reclassifying mirrored podcasts as YouTube
# upstream, so only un-mirrored audio reaches the fetcher's podcast
# handler (Whisper). Extend when the PDF fetcher port lands.
SUPPORTED_CONTENT_TYPES: tuple[str, ...] = ("YouTube", "arXiv", "Article", "Other", "Podcast")

# Active prompt labels — basenames of the markdown files under prompts/.
# Code-level constants (not env vars) because prompt versions don't vary
# per-deployment: dev and prod ship the same prompt until somebody bumps
# the version here. Same pattern as the `_DAG_VERSION` constants in
# orchestrators/config.py — manual bump on a prompt-shape change.
PROMPT_LABEL_NARRATIVE = "narrative_v1"
PROMPT_LABEL_TOPIC_CARD = "topic_card_v1"
PROMPT_LABEL_FOLLOWUPS = "followups_v1"

__all__ = [
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
