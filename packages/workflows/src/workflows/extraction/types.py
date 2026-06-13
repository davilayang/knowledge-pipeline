"""Shared types for `workflows.extraction`.

`PromptBundle` is the prompt set bound to a single `content_shape`: three
(text, label) pairs — one per role — that the three-call extractor fires
together. Per-call sha256 is computed by `ThreeCallOpenAIExtractor` so the
record-writing path doesn't have to.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptBundle:
    """Three-prompt set for one content_shape.

    Each field is a `(prompt_text, prompt_label)` pair. The text drives the
    OpenAI call; the label is recorded on each `ExtractionCallRecord` so
    downstream eval queries can group runs by prompt version. The extractor
    hashes the text into `prompt_sha256` for per-call staleness comparison.
    """

    narrative: tuple[str, str]
    topic_card: tuple[str, str]
    followups: tuple[str, str]
