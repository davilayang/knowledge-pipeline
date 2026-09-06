"""The extraction prompt files the service actually ships.

Every other extraction test uses placeholder prompts, on purpose — none of them
is about prompt wording, and pointing them at the real files would make an
unrelated prompt edit fail them. That leaves one thing unchecked, and it is the
thing that breaks in production rather than in CI: a default label naming a file
nobody shipped. This module checks exactly that, against the real tree.
"""

from pathlib import Path

import pytest

from fetcher.extract.prompts import UnknownPromptVersion, load_prompt
from fetcher.extract.tasks import TASKS


# services/fetcher/tests/ -> services/fetcher/ -> services/ -> repo root
REPO_PROMPTS = Path(__file__).resolve().parents[3] / "prompts"


# A phrase from each prompt's body, distinctive enough that another prompt would
# not contain it. Existence alone would not catch a label pointed at the wrong
# file — which is the mistake a label makes possible in the first place.
_MARKERS = {
    "metadata": "publisher",
    "narrative": "core_idea",
    "topic_card": "PER-FIELD CONTRACTS",
    "followups": "follow-up questions",
}


@pytest.mark.parametrize("task_name", sorted(TASKS))
def test_every_default_prompt_label_names_the_right_shipped_file(task_name: str) -> None:
    body = load_prompt(TASKS[task_name].default_prompt_label, prompts_root=REPO_PROMPTS)
    assert body.strip(), f"{task_name}'s prompt is empty once its design notes are stripped"
    assert _MARKERS[task_name] in body, (
        f"{task_name} resolves to {TASKS[task_name].default_prompt_label}.md, which does "
        f"not read like a {task_name} prompt"
    )


def test_a_label_may_not_escape_the_prompts_directory() -> None:
    """A label is a flat filename. Anything with a path in it is a caller
    reaching outside the tree, and is refused rather than resolved."""
    with pytest.raises(UnknownPromptVersion):
        load_prompt("../../etc/passwd", prompts_root=REPO_PROMPTS)
