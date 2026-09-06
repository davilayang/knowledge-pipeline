"""Async batch fetch endpoints."""

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from domains.fetch_store.sources import (
    get_job,
    get_job_status,
    insert_job,
    update_job,
)
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from fetcher import task_registry
from fetcher.endpoints.errors import problem_response
from fetcher.errors import BadUrl, UnsupportedKind
from fetcher.problems import problem_body
from fetcher.registry import find_handler
from fetcher.types import FetchRequest
from fetcher.endpoints.schemas import ProblemResponse
from fetcher.workers import new_batch_id, new_job_id, spawn_job


router = APIRouter(tags=["Fetch"])


class FetchBatch(BaseModel):
    requests: list[FetchRequest]


def _is_valid_url_shape(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


@router.post(
    "/v1/fetches",
    summary="Async-job: submit a batch of URLs for background fetching.",
    responses={
        400: {
            "model": ProblemResponse,
            "description": "Batch is empty, exceeds `batch_max`, or contains a malformed URL.",
        },
    },
)
async def post_fetches(batch: FetchBatch, request: Request) -> Response:
    settings = request.app.state.settings
    ctx = request.app.state.fetch_context
    db_path = Path(settings.db_path)

    if len(batch.requests) > settings.batch_max:
        return problem_response(
            status=400,
            code="BATCH_TOO_LARGE",
            title=f"Batch exceeds FETCHER_BATCH_MAX={settings.batch_max}",
            detail=f"received {len(batch.requests)} items, max is {settings.batch_max}",
            instance="/v1/fetches",
            retryable=False,
        )

    batch_id = new_batch_id()
    items: list[dict[str, Any]] = []
    for item in batch.requests:
        job_id = new_job_id()
        request_body = item.model_dump()

        # Eager validation to match previous behavior
        validation_error = None
        if not _is_valid_url_shape(item.url):
            validation_error = problem_body(
                status=BadUrl.status,
                code=BadUrl.code,
                title=BadUrl.title,
                detail=f"malformed URL: {item.url}",
                instance=f"/v1/fetches/{job_id}",
                retryable=BadUrl.retryable,
            )
        elif find_handler(item.url) is None:
            validation_error = problem_body(
                status=UnsupportedKind.status,
                code=UnsupportedKind.code,
                title=UnsupportedKind.title,
                detail=f"no handler matches URL: {item.url}",
                instance=f"/v1/fetches/{job_id}",
                retryable=UnsupportedKind.retryable,
            )

        expires_at = insert_job(
            db_path=db_path,
            job_id=job_id,
            batch_id=batch_id,
            job_type="fetch",
            request_body=request_body,
        )

        if validation_error:
            update_job(db_path=db_path, job_id=job_id, status="failed", error=validation_error)
            items.append(
                {
                    "job_id": job_id,
                    "status_url": f"/v1/fetches/{job_id}",
                    "expires_at": expires_at,
                    "error": validation_error,
                }
            )
        else:
            spawn_job(job_id, request_body, settings=settings, ctx=ctx)
            items.append(
                {
                    "job_id": job_id,
                    "status_url": f"/v1/fetches/{job_id}",
                    "expires_at": expires_at,
                }
            )

    return Response(
        status_code=202,
        content=json.dumps({"fetches": items}),
        media_type="application/json",
    )


@router.get(
    "/v1/fetches/{job_id}",
    summary="Async-job: poll for the status + result of a previously-submitted fetch.",
)
async def get_fetch(job_id: str, request: Request) -> dict[str, Any]:
    body = get_job(db_path=Path(request.app.state.settings.db_path), job_id=job_id)
    if body is None:
        raise HTTPException(status_code=404, detail="job not found")
    return body


@router.delete(
    "/v1/fetches/{job_id}",
    summary="Async-job: cancel a pending or in-flight fetch by job id.",
    responses={
        409: {
            "model": ProblemResponse,
            "description": "Job already in a terminal state — nothing to cancel.",
        },
        499: {
            "model": ProblemResponse,
            "description": "Client-side cancellation acknowledgement.",
        },
    },
)
async def delete_fetch(job_id: str, request: Request) -> Response:
    settings = request.app.state.settings
    db_path = Path(settings.db_path)

    status = get_job_status(db_path=db_path, job_id=job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="job not found")
    if status in {"done", "failed"}:
        return problem_response(
            status=409,
            code="JOB_TERMINAL",
            title="Job is in a terminal state",
            detail=f"current status: {status}",
            instance=f"/v1/fetches/{job_id}",
            retryable=False,
        )

    task_registry.cancel(job_id)

    status_after_cancel = get_job_status(db_path=db_path, job_id=job_id)
    if status_after_cancel in {"pending", "running"}:
        update_job(
            db_path=db_path,
            job_id=job_id,
            status="failed",
            error=problem_body(
                status=499,
                code="CANCELLED",
                title="Job was cancelled",
                detail="DELETE /v1/fetches/{job_id}",
                instance=f"/v1/fetches/{job_id}",
                retryable=False,
            ),
        )

    return Response(status_code=204)
