"""Production `chat_fn` builders for the wiki judges.

Isolated from `judges.py` (which stays provider-free) — this is the only wiki-eval
module that imports `workflows.llm`. A builder returns a `Callable[[str], dict]`
that runs the judge LLM with structured output and hands the judge a plain dict.
An optional `calls_sink` collects each `LLMCall` so the benchmark can persist cost.
"""

from collections.abc import Callable

from pydantic import BaseModel
from workflows.llm import LLMCall, generate_structured_with_usage

JUDGE_MODEL = "gpt-4.1"  # stronger than the gpt-4.1-mini synthesiser (less self-bias)


class _ClaimModel(BaseModel):
    text: str
    supported: bool
    evidence: str | None = None


class FaithfulnessClaimsModel(BaseModel):
    claims: list[_ClaimModel]


def make_faithfulness_chat_fn(
    *,
    model: str = JUDGE_MODEL,
    calls_sink: list[LLMCall] | None = None,
) -> Callable[[str], dict]:
    """Build the faithfulness judge's `chat_fn` over structured LLM output."""

    def chat_fn(prompt: str) -> dict:
        parsed, call = generate_structured_with_usage(
            prompt, schema=FaithfulnessClaimsModel, model=model
        )
        if calls_sink is not None:
            calls_sink.append(call)
        return parsed.model_dump()

    return chat_fn
