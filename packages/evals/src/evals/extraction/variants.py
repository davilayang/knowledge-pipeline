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

from workflows.extraction import ThreeCallOpenAIExtractor

from evals.core import FixtureRun, RunStatus, Variant, VariantProvenance
from evals.extraction.types import ExtractionFixture


def _call_extractor(extractor: ThreeCallOpenAIExtractor, content: str, content_type: str) -> Any:
    """Invoke the extractor regardless of whether a loop is already running.

    ThreeCallOpenAIExtractor.extract() wraps its async pipeline with asyncio.run(),
    which raises RuntimeError when called inside an existing event loop (Jupyter
    kernels are the common case). When we detect a running loop, hop into a
    short-lived thread so the extractor's asyncio.run gets its own loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return extractor.extract(content, content_type=content_type)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(extractor.extract, content, content_type=content_type).result()


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
) -> Variant:
    """Build a Variant that runs the three-call extractor on one fixture.

    `prompt_versions` is reflected verbatim in `VariantProvenance.prompt_versions`.
    `cost_estimator(tokens_in, tokens_out) -> usd` populates `FixtureRun.cost_usd`;
    defaults to $0.0 so callers without pricing data still see a structurally
    complete record.
    """
    config = {
        "extractor": "ThreeCallOpenAIExtractor",
        "model": model,
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

    def _run(fixture: ExtractionFixture) -> FixtureRun:
        extractor = ThreeCallOpenAIExtractor(
            api_key=api_key,
            model=model,
            narrative_prompt=narrative_prompt_text,
            narrative_prompt_label=prompt_versions.get("narrative", "unknown"),
            topic_card_prompt=topic_card_prompt_text,
            topic_card_prompt_label=prompt_versions.get("topic_card", "unknown"),
            followups_prompt=followups_prompt_text,
            followups_prompt_label=prompt_versions.get("followups", "unknown"),
        )
        t0 = time.monotonic()
        try:
            payload, records = _call_extractor(extractor, fixture.content, fixture.content_type)
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
