"""Content shape constants.

The shape vocabulary that the triage pipeline writes to queue.db and
Notion. Classification logic lives in `content_shape_llm.py` (LLM
primary) + URL-deterministic fast-paths in `assets.py` (arXiv,
audio URLs).
"""

SHAPE_CONFERENCE_TALK = "conference_talk"
SHAPE_PODCAST_EPISODE = "podcast_episode"
SHAPE_TUTORIAL = "tutorial"
SHAPE_OPINION_ESSAY = "opinion_essay"
SHAPE_RESEARCH_SUMMARY = "research_summary"
SHAPE_UNKNOWN = "unknown"

ALL_CONTENT_SHAPES = {
    SHAPE_CONFERENCE_TALK,
    SHAPE_PODCAST_EPISODE,
    SHAPE_TUTORIAL,
    SHAPE_OPINION_ESSAY,
    SHAPE_RESEARCH_SUMMARY,
    SHAPE_UNKNOWN,
}
