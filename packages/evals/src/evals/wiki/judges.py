"""Wiki page-quality judges (eval Phase 1).

Reference-light, source-grounded judges over a synthesised wiki page. Each judge
is a frozen dataclass with an injected `chat_fn: Callable[[str], dict]` (matches
`evals.core.judges.LLMJudge`): the judge assembles the prompt, `chat_fn` runs the
judge LLM and returns parsed JSON. Tests pass a stub; production wires a thin
wrapper around `workflows.llm.generate_structured_with_usage`.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

FAITHFULNESS_PROMPT = """\
You are grading a wiki page for faithfulness to its sources. Decompose the page
into atomic factual claims. For EACH claim decide whether it is directly
supported by the SOURCES below; quote the supporting span as evidence, or null
if unsupported.

Return JSON with a "claims" array; each item has "text" (the claim), "supported"
(boolean), and "evidence" (a source quote or null).

SOURCES:
{sources}

PAGE:
{page}
"""


def _grounding_block(sources: Sequence[str], prior_sources: Sequence[str]) -> str:
    """Assemble the grounding text. For UPDATE pages, prior sources are included
    (labelled) so claims carried over from earlier sources aren't falsely flagged
    unsupported when only the new source is shown."""
    new = "\n\n---\n\n".join(sources)
    if not prior_sources:
        return new
    prior = "\n\n---\n\n".join(prior_sources)
    return f"PRIOR SOURCES (already reflected in the page):\n{prior}" f"\n\nNEW SOURCES:\n{new}"


@dataclass(frozen=True)
class Claim:
    text: str
    supported: bool
    evidence: str | None = None


@dataclass(frozen=True)
class FaithfulnessScore:
    claims: list[Claim]
    unsupported_count: int
    grounded_fraction: float
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FaithfulnessJudge:
    chat_fn: Callable[[str], dict]
    prompt_template: str = FAITHFULNESS_PROMPT

    def score(
        self,
        *,
        page: str,
        sources: Sequence[str],
        prior_sources: Sequence[str] = (),
    ) -> FaithfulnessScore:
        prompt = self.prompt_template.format(
            page=page, sources=_grounding_block(sources, prior_sources)
        )
        raw = self.chat_fn(prompt)
        if not isinstance(raw.get("claims"), list):
            raise ValueError(
                f"faithfulness judge returned no 'claims' array (got keys: {sorted(raw)})"
            )
        claims = [
            Claim(
                text=c["text"],
                supported=bool(c["supported"]),
                evidence=c.get("evidence"),
            )
            for c in raw["claims"]
        ]
        unsupported = sum(1 for c in claims if not c.supported)
        grounded = (len(claims) - unsupported) / len(claims) if claims else 1.0
        return FaithfulnessScore(
            claims=claims,
            unsupported_count=unsupported,
            grounded_fraction=grounded,
            metadata={"raw": raw},
        )
