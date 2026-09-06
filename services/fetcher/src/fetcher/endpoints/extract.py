"""POST /v1/extract: structured LLM extraction over already-fetched content."""

from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from fetcher.endpoints.errors import problem_response
from fetcher.endpoints.schemas import ProblemResponse
from fetcher.extract.cache import cache_key, read as cache_read, write as cache_write
from fetcher.extract.openai_lane import (
    MAX_TOKENS,
    TaskOutcome,
    build_role_prompt,
    effective_prompt_sha,
    run_tasks,
    token_kwargs,
)
from fetcher.extract.prompts import UnknownPromptVersion, load_prompt
from fetcher.extract.tasks import TASKS, TaskSpec, execution_order


router = APIRouter(tags=["Extract"])

# The only lane implemented. Recorded on every call and folded into every cache
# key, so adding a second provider cannot silently reuse this one's results.
_PROVIDER = "openai"


class TaskRequest(BaseModel):
    """One requested task, with an optional prompt override.

    `prompt_version` names a file in the prompts directory, never prompt text:
    a label fails closed on an unknown value, where accepting text would make
    the endpoint an injection surface and hollow out its reason to exist.
    """

    task: str
    prompt_version: str | None = None


class ExtractRequest(BaseModel):
    content: str
    content_type: str
    user_notes: str | None = None
    model: str | None = None
    tasks: list[str | TaskRequest]


def _normalise(tasks: list[str | TaskRequest]) -> list[TaskRequest]:
    return [TaskRequest(task=t) if isinstance(t, str) else t for t in tasks]


@router.get(
    "/v1/extract/prompts",
    summary="The model, prompt label and staleness sha each task would run with.",
    responses={
        400: {"model": ProblemResponse, "description": "A configured prompt label has no file."},
    },
)
async def extract_prompts(request: Request) -> Any:
    """Report what a run would send, without sending it.

    A caller that decides whether an extraction stored earlier is still current
    needs the model and the sha *before* it commits to a call. The only other way to get it is
    to re-derive it — which means re-implementing the shared system message, the
    article envelope and the generated schema block on the caller's side, and
    keeping all three in step with this service forever. That duplication is
    what this service exists to remove, so it answers the question instead.
    """
    settings = request.app.state.settings
    prompts_root = Path(settings.extraction_prompts_root)
    prompts = []
    for spec in TASKS.values():
        label = spec.default_prompt_label
        try:
            text = load_prompt(label, prompts_root=prompts_root)
        except UnknownPromptVersion:
            return problem_response(
                status=400,
                code="UNKNOWN_PROMPT_VERSION",
                title="Configured prompt is missing",
                detail=f"task {spec.name!r} names prompt {label!r}, which is not on disk",
                instance=str(request.url.path),
                retryable=False,
            )
        prompts.append(
            {
                "task": spec.name,
                "prompt_label": label,
                # No reader notes: their fold is per-request, and a caller asking
                # what the defaults are has not got a request yet.
                "prompt_sha256": effective_prompt_sha(
                    build_role_prompt(spec, text, user_notes=None), spec.schema
                ),
            }
        )
    return {"model": settings.extraction_model, "prompts": prompts}


@router.post(
    "/v1/extract",
    summary="Run one or more structured extraction tasks over a fetched body.",
    responses={
        400: {
            "model": ProblemResponse,
            "description": "Empty content, or an unknown/duplicated task name.",
        },
    },
)
async def extract(req: ExtractRequest, request: Request) -> Any:
    if not req.content.strip():
        return problem_response(
            status=400,
            code="BAD_REQUEST",
            title="Empty content",
            detail="content must be non-empty",
            instance=str(request.url.path),
            retryable=False,
        )

    requested = _normalise(req.tasks)

    unknown = sorted({t.task for t in requested} - set(TASKS))
    if unknown:
        return problem_response(
            status=400,
            code="INVALID_TASK",
            title="Unknown extraction task",
            detail=f"unknown tasks {unknown}; this service extracts {sorted(TASKS)}",
            instance=str(request.url.path),
            retryable=False,
        )

    counts = Counter(t.task for t in requested)
    repeated = sorted(name for name, count in counts.items() if count > 1)
    if repeated:
        return problem_response(
            status=400,
            code="DUPLICATE_TASK",
            title="Task requested more than once",
            detail=(
                f"tasks {repeated} appear more than once; each task runs once per "
                "request, so a repeat can only be a caller mistake"
            ),
            instance=str(request.url.path),
            retryable=False,
        )

    settings = request.app.state.settings
    prompts_root = Path(settings.extraction_prompts_root)
    overrides = {t.task: t.prompt_version for t in requested if t.prompt_version}

    # Every prompt is resolved up front. Resolving lazily would let a typo in the
    # last task's label surface only after the earlier ones had been billed.
    plan: list[tuple[TaskSpec, str, str]] = []
    for spec in execution_order({t.task for t in requested}):
        label = overrides.get(spec.name, spec.default_prompt_label)
        try:
            plan.append((spec, label, load_prompt(label, prompts_root=prompts_root)))
        except UnknownPromptVersion:
            return problem_response(
                status=400,
                code="UNKNOWN_PROMPT_VERSION",
                title="Unknown prompt version",
                detail=(
                    f"no prompt {label!r} for task {spec.name!r}; a prompt_version "
                    "names a file the service ships, not prompt text"
                ),
                instance=str(request.url.path),
                retryable=False,
            )

    model = req.model or settings.extraction_model
    if not model or not settings.openai_api_key:
        return problem_response(
            status=503,
            code="EXTRACTION_UNCONFIGURED",
            title="Extraction not configured",
            detail="set OPENAI_API_KEY and EXTRACT_QUEUE_MODEL to serve /v1/extract",
            instance=str(request.url.path),
            retryable=False,
        )

    db_path = Path(settings.db_path)
    keys = {
        spec.name: cache_key(
            task=spec.name,
            content=req.content,
            content_type=req.content_type,
            user_notes=req.user_notes,
            prompt_sha256=effective_prompt_sha(
                build_role_prompt(spec, text, user_notes=req.user_notes), spec.schema
            ),
            provider=_PROVIDER,
            model=model,
            generation=token_kwargs(model, MAX_TOKENS),
        )
        for spec, _, text in plan
    }

    results: dict[str, dict[str, Any]] = {}
    calls: dict[str, dict[str, Any]] = {}
    cache_hits: list[str] = []
    to_run = []
    for spec, label, text in plan:
        hit = cache_read(db_path=db_path, key=keys[spec.name])
        if hit is None:
            to_run.append((spec, label, text))
            continue
        payload, call = hit
        results[spec.name] = {"task": spec.name, "schema_version": 1, "payload": payload}
        calls[spec.name] = call
        cache_hits.append(spec.name)

    errors: dict[str, dict[str, Any]] = {}
    if to_run:
        outcomes = await run_tasks(
            to_run,
            content=req.content,
            content_type=req.content_type,
            user_notes=req.user_notes,
            model=model,
            api_key=settings.openai_api_key,
        )
        for outcome in outcomes:
            call = _call_record(outcome, model=model)
            calls[outcome.task] = call
            if outcome.payload is None:
                errors[outcome.task] = {
                    "task": outcome.task,
                    "code": "TASK_FAILED",
                    "detail": outcome.error,
                    "retryable": outcome.retryable,
                    "blocked_by": None,
                }
                continue
            payload = outcome.payload.model_dump(mode="json")
            results[outcome.task] = {
                "task": outcome.task,
                "schema_version": 1,
                "payload": payload,
            }
            cache_write(
                db_path=db_path,
                key=keys[outcome.task],
                task=outcome.task,
                payload=payload,
                call=call,
                ttl_days=settings.cache_ttl_days,
            )

    # Reported in lane order, not in whichever order cache hits and fresh calls
    # happened to resolve, so a response reads the same however it was served.
    order = [spec.name for spec, _, _ in plan]
    return {
        "results": [results[name] for name in order if name in results],
        "errors": [errors[name] for name in order if name in errors],
        "calls": [calls[name] for name in order if name in calls],
        "cache_hits": [name for name in order if name in cache_hits],
    }


def _call_record(outcome: TaskOutcome, *, model: str) -> dict[str, Any]:
    """One row of the ledger the caller writes — `ExtractionCallRecord` made
    visible on the wire, plus the provider that produced it."""
    return {
        "task": outcome.task,
        "prompt_label": outcome.prompt_label,
        "prompt_sha256": outcome.prompt_sha256,
        "provider": _PROVIDER,
        "model": model,
        "tokens_in": outcome.tokens_in,
        "tokens_out": outcome.tokens_out,
        "cached_tokens": outcome.cached_tokens,
        "duration_ms": round(outcome.duration_ms, 1),
    }
