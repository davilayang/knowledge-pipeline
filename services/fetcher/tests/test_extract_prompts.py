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


@pytest.mark.parametrize("task_name", sorted(TASKS))
def test_every_default_prompt_label_names_a_shipped_file(task_name: str) -> None:
    body = load_prompt(TASKS[task_name].default_prompt_label, prompts_root=REPO_PROMPTS)
    assert body.strip(), f"{task_name}'s prompt is empty once its design notes are stripped"


def test_a_label_may_not_escape_the_prompts_directory() -> None:
    """A label is a flat filename. Anything with a path in it is a caller
    reaching outside the tree, and is refused rather than resolved."""
    with pytest.raises(UnknownPromptVersion):
        load_prompt("../../etc/passwd", prompts_root=REPO_PROMPTS)
