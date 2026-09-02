"""Variant builders for the extraction pipeline.

Wraps `workflows.extraction.ThreeCallOpenAIExtractor` into a `Variant`. The
variant `run` callable accepts an `ExtractionFixture` and returns a
`FixtureRun` with the payload dict + per-call token totals.

Prompts are accepted as text (not labels / paths). Callers — notebooks or
benchmark CLI — resolve labels to text up front (`prompts/extraction/<label>.md`
read) and pass the text in. This keeps env/label/path concerns out of evals.
"""

import asyncio
import concurrent.futures
import hashlib
import time
from collections.abc import Callable
from typing import Any

from domains.extraction.schemas import Narrative
from workflows.extraction import PromptBundle, ThreeCallOpenAIExtractor

from evals.core import FixtureRun, RunStatus, Variant, VariantProvenance
from evals.extraction.types import ExtractionFixture


def _call_extractor(
    extractor: ThreeCallOpenAIExtractor,
    content: str,
    content_type: str,
    content_shape: str,
) -> Any:
    """Invoke the extractor regardless of whether a loop is already running.

    ThreeCallOpenAIExtractor.extract() wraps its async pipeline with asyncio.run(),
    which raises RuntimeError when called inside an existing event loop (Jupyter
    kernels are the common case). When we detect a running loop, hop into a
    short-lived thread so the extractor's asyncio.run gets its own loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return extractor.extract(content, content_type=content_type, content_shape=content_shape)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(
            extractor.extract,
            content,
            content_type=content_type,
            content_shape=content_shape,
        ).result()


def _reject_prompt_written_for_another_schema(narrative_prompt_text: str, label: str) -> None:
    """Refuse a narrative prompt that does not name every field of `Narrative`.

    `shared_prefix.schema_block()` generates the field list from the model and
    appends it at call time, so an older prompt body still *runs*: the model is
    told to emit today's fields while the body describes yesterday's. The output
    then gets scored and tabulated as that prompt's result, which is a wrong
    number that looks like a measurement — worse than an error.

    Checking that the body mentions each field name is crude, but it separates a
    candidate prompt written against the current schema from one that was not,
    which is the only distinction that matters here.
    """
    missing = [f for f in Narrative.model_fields if f not in narrative_prompt_text]
    if missing:
        raise ValueError(
            f"narrative prompt {label!r} never mentions {', '.join(missing)}, so it was "
            f"written for a different `Narrative` shape. The extractor generates the field "
            f"list from the current model, so running this would ask the model for today's "
            f"fields while the prompt describes another set, and score the result as if it "
            f"were this prompt's. To compare against an older prompt, run it from a checkout "
            f"of the release that carried it and compare the recorded means."
        )


def make_three_call_variant(
    *,
    name: str,
    narrative_prompt_text: str,
    topic_card_prompt_text: str,
    followups_prompt_text: str,
    prompt_versions: dict[str, str],
    model: str,
    api_key: str,
    code_revision: str = "unknown",
    corpus_anchor: str | None = None,
    output_schema_version: int = 1,
    cost_estimator: Callable[[int, int], float] | None = None,
    max_tokens: int = 4096,
) -> Variant:
    """Build a Variant that runs the three-call extractor on one fixture.

    `prompt_versions` is reflected verbatim in `VariantProvenance.prompt_versions`.
    `cost_estimator(tokens_in, tokens_out) -> usd` populates `FixtureRun.cost_usd`;
    defaults to $0.0 so callers without pricing data still see a structurally
    complete record.

    `max_tokens` (default 4096) is threaded to the extractor so the harness
    measures the same output ceiling as prod — a lower value silently truncates
    rich narratives and under-measures coverage.
    """
    config = {
        "extractor": "ThreeCallOpenAIExtractor",
        "model": model,
        "max_tokens": max_tokens,
        "narrative_prompt_sha": _sha_short(narrative_prompt_text),
        "topic_card_prompt_sha": _sha_short(topic_card_prompt_text),
        "followups_prompt_sha": _sha_short(followups_prompt_text),
    }
    provenance = VariantProvenance(
        prompt_versions=dict(prompt_versions),
        model_versions={"extraction": model},
        code_revision=code_revision,
        corpus_anchor=corpus_anchor,
        output_schema_version=output_schema_version,
    )
    cost_fn = cost_estimator or (lambda _in, _out: 0.0)

    _reject_prompt_written_for_another_schema(
        narrative_prompt_text, prompt_versions.get("narrative", name)
    )

    def _run(fixture: ExtractionFixture) -> FixtureRun:
        bundle = PromptBundle(
            narrative=(narrative_prompt_text, prompt_versions.get("narrative", "unknown")),
            topic_card=(topic_card_prompt_text, prompt_versions.get("topic_card", "unknown")),
            followups=(followups_prompt_text, prompt_versions.get("followups", "unknown")),
        )
        extractor = ThreeCallOpenAIExtractor(
            api_key=api_key,
            model=model,
            prompt_sets={"unknown": bundle},
            max_tokens=max_tokens,
        )
        t0 = time.monotonic()
        try:
            # content_shape="unknown" is deliberate: the extractor routes on its
            # own shape taxonomy (only the generic bundle is registered), whereas
            # ExtractionFixture.content_shape is a reporting-only label (prose/
            # survey/…) used for stratification — the two are distinct axes.
            payload, records = _call_extractor(
                extractor,
                fixture.content,
                fixture.content_type,
                content_shape="unknown",
            )
        except Exception as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            return FixtureRun(
                fixture_id=fixture.fixture_id,
                status=RunStatus.ERROR,
                output=None,
                stages=[],
                tokens_in=0,
                tokens_out=0,
                cost_usd=0.0,
                duration_ms=duration_ms,
                error_message=str(e),
            )
        duration_ms = int((time.monotonic() - t0) * 1000)
        tokens_in = sum(r.tokens_in for r in records)
        tokens_out = sum(r.tokens_out for r in records)
        return FixtureRun(
            fixture_id=fixture.fixture_id,
            status=RunStatus.SUCCESS,
            output={
                "narrative_md": payload.narrative_md,
                "topic_card": payload.topic_card.model_dump(),
                "followups": payload.followups.model_dump(),
            },
            stages=[],
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_fn(tokens_in, tokens_out),
            duration_ms=duration_ms,
        )

    return Variant(
        name=name,
        config=config,
        provenance=provenance,
        run=_run,
    )


def _sha_short(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]
