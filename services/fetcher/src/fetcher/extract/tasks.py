"""What the extraction lane can be asked for, and in what order it runs.

Task names are a closed set. An unrecognised one is a caller bug — a typo, or a
client built against a newer service — and is rejected before any model call, so
nobody pays for a batch that quietly did less than it asked for.
"""

from dataclasses import dataclass

from domains.extraction.schemas import Followups, MetadataPayload, Narrative, TopicCard


@dataclass(frozen=True)
class TaskSpec:
    """One extractable output: the model it validates against and the prompt
    that asks for it."""

    name: str
    schema: type
    default_prompt_label: str


# Declaration order is execution order, load-bearing on OpenAI: every task sends
# the same article prefix, so the first pays the prompt-cache write and the rest
# read it. `metadata` leads because knowledge-pipeline gates on it — an unusable
# body should cost one call, not four.
_TASKS: tuple[TaskSpec, ...] = (
    TaskSpec("metadata", MetadataPayload, "metadata_v1"),
    TaskSpec("narrative", Narrative, "narrative_v3"),
    TaskSpec("topic_card", TopicCard, "topic_card_v1"),
    TaskSpec("followups", Followups, "followups_v1"),
)

TASKS: dict[str, TaskSpec] = {spec.name: spec for spec in _TASKS}


def execution_order(names: set[str]) -> list[TaskSpec]:
    """The requested tasks in the lane's fixed order, whatever order they were
    asked for in. Callers do not choose — see the note on `_TASKS`."""
    return [spec for spec in _TASKS if spec.name in names]
