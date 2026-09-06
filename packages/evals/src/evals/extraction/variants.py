"""Variant builders for the extraction pipeline.

Wraps the fetcher service's `POST /v1/extract` into a `Variant`. The variant's
`run` callable accepts an `ExtractionFixture` and returns a `FixtureRun` with the
payload dict + per-call token totals.

Prompts are named, not supplied: a variant passes a `prompt_version` per task and
the service resolves it to a file it ships, so a score always names a prompt that
exists and can be re-run from its label. Trying a candidate means writing it into
`prompts/extraction/` first — which is what keeps the harness measuring
production rather than a drifted copy of it.
"""

import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from domains.extraction.prompts import strip_design_notes
from domains.extraction.render import render_narrative
from domains.extraction.schemas import Followups, Narrative, TopicCard

from evals.core import FixtureRun, RunStatus, Variant, VariantProvenance
from evals.extraction.types import ExtractionFixture

# The tasks a reading card is made of, in the order the service runs them.
_TASKS = ("narrative", "topic_card", "followups")


def _default_prompts_dir() -> Path:
    """Repo-root `prompts/extraction/`, found by walking up from this file.

    Read only to check a candidate against the current schema before a run costs
    anything; the service resolves the label it actually uses.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "prompts" / "extraction").is_dir():
            return parent / "prompts" / "extraction"
    raise FileNotFoundError("no prompts/extraction/ above evals.extraction.variants")


def _sha_short(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _reject_prompt_written_for_another_schema(narrative_prompt_text: str, label: str) -> None:
    """Refuse a narrative prompt that does not name every field of `Narrative`.

    The service appends the generated field list at call time, so an older body
    still *runs*: the model is asked for today's fields while the body describes
    yesterday's, and the result is scored as that prompt's — a wrong number
    wearing the shape of a measurement. Checking that the body mentions each
    field name is crude, but it is the only distinction that matters here.
    """
    missing = [f for f in Narrative.model_fields if f not in narrative_prompt_text]
    if missing:
        raise ValueError(
            f"narrative prompt {label!r} never mentions {', '.join(missing)}, so it was "
            f"written for a different `Narrative` shape. The service generates the field "
            f"list from the current model, so running this would ask the model for today's "
            f"fields while the prompt describes another set, and score the result as if it "
            f"were this prompt's. To compare against an older prompt, run it from a checkout "
            f"of the release that carried it and compare the recorded means."
        )


def make_three_call_variant(
    *,
    name: str,
    prompt_versions: dict[str, str],
    model: str,
    service_url: str,
    prompts_dir: Path | None = None,
    code_revision: str = "unknown",
    corpus_anchor: str | None = None,
    output_schema_version: int = 1,
    cost_estimator: Callable[[int, int], float] | None = None,
    timeout_s: float = 300.0,
) -> Variant:
    """Build a Variant that extracts one fixture through the fetcher service.

    `prompt_versions` maps narrative / topic_card / followups to labels the
    service ships, reflected verbatim in `VariantProvenance.prompt_versions`.
    `cost_estimator(tokens_in, tokens_out) -> usd` fills `FixtureRun.cost_usd`,
    defaulting to $0.0. The token ceiling is deliberately not a parameter: a
    harness measuring a different one from production would under-measure
    coverage on exactly the long sources that stress it.
    """
    directory = prompts_dir or _default_prompts_dir()
    texts = {
        task: strip_design_notes((directory / f"{prompt_versions[task]}.md").read_text())
        for task in _TASKS
    }
    _reject_prompt_written_for_another_schema(
        texts["narrative"], prompt_versions.get("narrative", name)
    )

    config = {
        "extractor": "fetcher:/v1/extract",
        "model": model,
        "service_url": service_url,
        **{f"{task}_prompt_sha": _sha_short(texts[task]) for task in _TASKS},
    }
    provenance = VariantProvenance(
        prompt_versions=dict(prompt_versions),
        model_versions={"extraction": model},
        code_revision=code_revision,
        corpus_anchor=corpus_anchor,
        output_schema_version=output_schema_version,
    )
    cost_fn = cost_estimator or (lambda _in, _out: 0.0)
    endpoint = f"{service_url.rstrip('/')}/v1/extract"

    def _run(fixture: ExtractionFixture) -> FixtureRun:
        t0 = time.monotonic()
        try:
            response = httpx.post(
                endpoint,
                timeout=timeout_s,
                json={
                    "content": fixture.content,
                    "content_type": fixture.content_type,
                    "model": model,
                    "tasks": [
                        {"task": task, "prompt_version": prompt_versions[task]} for task in _TASKS
                    ],
                },
            )
            output, tokens_in, tokens_out = _read_response(response)
        except Exception as exc:  # noqa: BLE001 — one fixture's failure is a row, not a crash
            return FixtureRun(
                fixture_id=fixture.fixture_id,
                status=RunStatus.ERROR,
                output=None,
                stages=[],
                tokens_in=0,
                tokens_out=0,
                cost_usd=0.0,
                duration_ms=int((time.monotonic() - t0) * 1000),
                error_message=str(exc),
            )
        return FixtureRun(
            fixture_id=fixture.fixture_id,
            status=RunStatus.SUCCESS,
            output=output,
            stages=[],
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_fn(tokens_in, tokens_out),
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    return Variant(name=name, config=config, provenance=provenance, run=_run)


def _read_response(response: httpx.Response) -> tuple[dict[str, Any], int, int]:
    """Turn one `/v1/extract` reply into the payload dict the scorers read.

    A partial batch is an error here, unlike in the pipeline: a number from two
    of three outputs is not comparable with one from three, and would be
    tabulated as though it were.
    """
    if response.status_code != 200:
        problem = response.json()
        raise RuntimeError(f"{problem.get('title', response.status_code)}: {problem.get('detail')}")
    body = response.json()
    if body.get("errors"):
        failed = ", ".join(f"{e['task']} ({e['detail']})" for e in body["errors"])
        raise RuntimeError(f"extraction incomplete: {failed}")
    payloads = {r["task"]: r["payload"] for r in body["results"]}
    return (
        {
            "narrative_md": render_narrative(Narrative.model_validate(payloads["narrative"])),
            "topic_card": TopicCard.model_validate(payloads["topic_card"]).model_dump(),
            "followups": Followups.model_validate(payloads["followups"]).model_dump(),
        },
        sum(c["tokens_in"] for c in body["calls"]),
        sum(c["tokens_out"] for c in body["calls"]),
    )
