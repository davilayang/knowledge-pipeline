"""Production `chat_fn` builders for the wiki judges.

Isolated from `judges.py` (which stays provider-free) — this is the only wiki-eval
module that imports `workflows.llm`. A builder returns a `Callable[[str], dict]`
that runs the judge LLM with structured output and hands the judge a plain dict.
An optional `calls_sink` collects each `LLMCall` so the benchmark can persist cost.
"""

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel
from workflows.llm import LLMCall, generate_structured_with_usage

JUDGE_MODEL = "gpt-4.1"  # stronger than the gpt-4.1-mini synthesiser (less self-bias)


class _ClaimModel(BaseModel):
    text: str
    supported: bool
    evidence: str | None = None


class FaithfulnessClaimsModel(BaseModel):
    claims: list[_ClaimModel]


class _NameOrgModel(BaseModel):
    anchor: str
    preserved: bool


class _QuoteModel(BaseModel):
    quote: str
    preserved: bool


class _AbstractionModel(BaseModel):
    source_specific: str
    page_placeholder: str


class SpecificityAnalysisModel(BaseModel):
    names_orgs: list[_NameOrgModel]
    quotes: list[_QuoteModel]
    abstractions: list[_AbstractionModel]


class _PassageModel(BaseModel):
    text: str
    on_topic: bool
    subject: str | None = None


class RelevancePassagesModel(BaseModel):
    passages: list[_PassageModel]


class _TagVerdictModel(BaseModel):
    correct_tag: Literal["fact", "speculation"]


class TaggingVerdictsModel(BaseModel):
    verdicts: list[_TagVerdictModel]


def _make_chat_fn(
    schema: type[BaseModel], model: str, calls_sink: list[LLMCall] | None
) -> Callable[[str], dict]:
    def chat_fn(prompt: str) -> dict:
        parsed, call = generate_structured_with_usage(prompt, schema=schema, model=model)
        if calls_sink is not None:
            calls_sink.append(call)
        return parsed.model_dump()

    return chat_fn


def make_faithfulness_chat_fn(
    *, model: str = JUDGE_MODEL, calls_sink: list[LLMCall] | None = None
) -> Callable[[str], dict]:
    """Build the faithfulness judge's `chat_fn` over structured LLM output."""
    return _make_chat_fn(FaithfulnessClaimsModel, model, calls_sink)


def make_specificity_chat_fn(
    *, model: str = JUDGE_MODEL, calls_sink: list[LLMCall] | None = None
) -> Callable[[str], dict]:
    """Build the specificity judge's `chat_fn` over structured LLM output."""
    return _make_chat_fn(SpecificityAnalysisModel, model, calls_sink)


def make_relevance_chat_fn(
    *, model: str = JUDGE_MODEL, calls_sink: list[LLMCall] | None = None
) -> Callable[[str], dict]:
    """Build the relevance judge's `chat_fn` over structured LLM output."""
    return _make_chat_fn(RelevancePassagesModel, model, calls_sink)


def make_tagging_chat_fn(
    *, model: str = JUDGE_MODEL, calls_sink: list[LLMCall] | None = None
) -> Callable[[str], dict]:
    """Build the tagging judge's `chat_fn` over structured LLM output."""
    return _make_chat_fn(TaggingVerdictsModel, model, calls_sink)
