"""Cite-by-index wide-schema extraction variant.

The wide arm replaces the fixed TopicCard (one core_mechanism / best_example /
main_tension …) with a length-scaled list of atomic Claims. Each claim points at
the source by **unit index** (`cited_indices`) into a pre-numbered source — not a
verbatim quote. The live run (2026-07-13) showed the model won't reproduce
verbatim spans (it elides/paraphrases), so quote-then-match grounded ~10%.
Citing an index sidesteps text matching entirely: index → decile is exact
localization, and faithfulness is a separate token check against the cited unit
(see evals.extraction.verify / coverage).

This mirrors newsletter-assistant's shipped grounding schema
(`Claim{text, cited_indices}`) so the two repos converge — NA's seam expects kp
to become the canonical unit provider it consumes.

Schemas live here in `evals`, NOT `domains`: the prod cross-repo TopicCard
contract stays untouched, so this workbench arm is self-contained and reversible.

extract_fn is an injected seam — `extract_fn(content) -> (output_dict, tokens_in,
tokens_out)`. Tests pass a stub; runtime wires the OpenAI structured-output call.
"""

import time
from collections.abc import Callable

from pydantic import BaseModel, Field

from evals.core import FixtureRun, RunStatus, Variant, VariantProvenance
from evals.extraction.types import ExtractionFixture
from evals.extraction.units import citable_units

# type ∈ mechanism|example|claim|tension|question|tieback — a light label on
# each claim, not a slot taxonomy that must stay in lockstep with TopicCard.
ClaimType = str


class Claim(BaseModel):
    text: str
    cited_indices: list[int] = Field(default_factory=list)
    type: ClaimType = "claim"


class WideOutput(BaseModel):
    extracted_title: str
    claims: list[Claim]


ExtractFn = Callable[[str], tuple[dict, int, int]]


def make_wide_variant(
    *,
    name: str,
    prompt_text: str,
    model: str,
    extract_fn: ExtractFn,
    code_revision: str = "unknown",
    corpus_anchor: str | None = None,
) -> Variant:
    """Build a Variant that runs the cite-by-index wide extractor on one fixture.

    `extract_fn(content)` returns `(output_dict, tokens_in, tokens_out)` where
    output_dict conforms to `WideOutput`. Injecting it keeps the LLM call out of
    this pure builder — tests stub it, runtime supplies the OpenAI-backed one.
    """
    config = {
        "extractor": "wide_cite_by_index",
        "model": model,
        "prompt_sha": _sha_short(prompt_text),
    }
    provenance = VariantProvenance(
        prompt_versions={"wide": _sha_short(prompt_text)},
        model_versions={"extraction": model},
        code_revision=code_revision,
        corpus_anchor=corpus_anchor,
        output_schema_version=1,
    )

    def _run(fixture: ExtractionFixture) -> FixtureRun:
        t0 = time.monotonic()
        try:
            output, tokens_in, tokens_out = extract_fn(fixture.content)
        except Exception as e:
            return FixtureRun(
                fixture_id=fixture.fixture_id,
                status=RunStatus.ERROR,
                output=None,
                stages=[],
                tokens_in=0,
                tokens_out=0,
                cost_usd=0.0,
                duration_ms=int((time.monotonic() - t0) * 1000),
                error_message=str(e),
            )
        return FixtureRun(
            fixture_id=fixture.fixture_id,
            status=RunStatus.SUCCESS,
            output=output,
            stages=[],
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=0.0,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    return Variant(name=name, config=config, provenance=provenance, run=_run)


# Appended to the prod topic_card body to turn the fixed-slot card into a
# length-scaled list of index-cited claims. Mirrors NA's compose_grounded_instruction.
WIDE_ITEMS_INSTRUCTION = """

---
OUTPUT SHAPE OVERRIDE (this run): the source is numbered line-by-line as `[i]`.
Instead of a fixed card, emit `claims` — a list of atomic claims scaled to the
source's richness, spread across the WHOLE document (beginning to end), not just
the opening. Each claim is one well-formed sentence obeying the per-field
grammatical contracts above, tagged with `type` (mechanism | example | claim |
tension | question | tieback), and MUST set `cited_indices`: the `[i]` index (or
indices) of the source line(s) that support it. Do not pad or repeat — one claim
per distinct idea.
"""


def number_source(content: str) -> tuple[str, list[str]]:
    """Return (numbered-source-text, units). The `[i]` numbering the model cites."""
    units = citable_units(content)
    numbered = "\n".join(f"[{i}] {u}" for i, u in enumerate(units))
    return numbered, units


def openai_wide_extract_fn(
    *, api_key: str, model: str, prompt_text: str, max_tokens: int = 8192
) -> ExtractFn:
    """Runtime `extract_fn`: numbers the source, runs an OpenAI structured-output
    call returning `(WideOutput dict, tokens_in, tokens_out)`. This is the
    untestable I/O seam — the variant + coverage logic is tested with a stub.
    """
    import openai

    client = openai.OpenAI(api_key=api_key)

    def _extract(content: str) -> tuple[dict, int, int]:
        numbered, _units = number_source(content)
        resp = client.beta.chat.completions.parse(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": prompt_text},
                {"role": "user", "content": numbered},
            ],
            response_format=WideOutput,
        )
        usage = resp.usage
        return (
            resp.choices[0].message.parsed.model_dump(),
            usage.prompt_tokens if usage else 0,
            usage.completion_tokens if usage else 0,
        )

    return _extract


def _sha_short(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()[:16]
