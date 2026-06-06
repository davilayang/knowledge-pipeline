"""Per-Topic-Card-field scorer — composes three judges from evals.core.judges.

Each field is mapped to a single judge; the overall score is the unweighted
mean of per-field scores. Callers inject `embed_fn` and `chat_fn` so the
scorer carries no provider dependency — tests pass stubs; runtime callers
wire OpenAI clients.

List-valued fields (`candidate_tie_backs`) are joined deterministically
before judging — order matters and is preserved.
"""

from collections.abc import Callable
from typing import Any

from evals.core import FieldScore
from evals.core.judges import EmbeddingSimilarityJudge, ExactMatchJudge, LLMJudge

# extracted_title moved from exact-match to embedding similarity after the
# first real benchmark run scored every fixture's title at 0.0 — LLMs basically
# never reproduce a hand-written reference title verbatim, so exact match is
# pure noise. ExactMatchJudge is retained for future use (e.g. tag-like fields).
_EXACT_FIELDS: tuple[str, ...] = ()
_EMBED_FIELDS = ("extracted_title", "core_mechanism", "transferable_pattern")
_LLM_FIELDS = ("best_example", "main_tension", "candidate_tie_backs")

DEFAULT_LLM_PROMPT = """\
You are a strict judge comparing two extractions per field. For each field in
{fields}, score the actual output against the expected output on a 0.0-1.0
scale where 1.0 means equivalent meaning and 0.0 means unrelated.

Expected: {expected}
Actual:   {actual}

Return a JSON object mapping each field name to a float score.
"""


class TopicCardScorer:
    def __init__(
        self,
        *,
        embed_fn: Callable[[str], list[float]],
        chat_fn: Callable[[str], dict],
        llm_prompt_template: str = DEFAULT_LLM_PROMPT,
    ) -> None:
        self._exact = ExactMatchJudge(fields=_EXACT_FIELDS)
        self._embed = EmbeddingSimilarityJudge(fields=_EMBED_FIELDS, embed_fn=embed_fn)
        self._llm = LLMJudge(
            fields=_LLM_FIELDS, chat_fn=chat_fn, prompt_template=llm_prompt_template
        )

    def score(
        self,
        *,
        expected: dict[str, Any],
        actual: dict[str, Any],
    ) -> FieldScore:
        exp = _flatten(expected)
        act = _flatten(actual)
        a = self._exact.score(expected=exp, actual=act)
        b = self._embed.score(expected=exp, actual=act)
        c = self._llm.score(expected=exp, actual=act)
        merged = {**a.value, **b.value, **c.value}
        overall = sum(merged.values()) / len(merged) if merged else 0.0
        return FieldScore(
            value={**merged, "__overall__": overall},
            metadata={
                "judge_per_field": {
                    **{f: "exact" for f in _EXACT_FIELDS},
                    **{f: "embedding" for f in _EMBED_FIELDS},
                    **{f: "llm" for f in _LLM_FIELDS},
                },
            },
        )


def _flatten(card: dict[str, Any]) -> dict[str, str]:
    """Coerce list-valued fields to strings; preserve order via newline join."""
    out: dict[str, str] = {}
    for k, v in card.items():
        if isinstance(v, list):
            out[k] = "\n".join(str(x) for x in v)
        elif v is None:
            out[k] = ""
        else:
            out[k] = str(v)
    return out
