"""Async batch fetch endpoints."""

import json
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from fetcher.db import open_connection
from fetcher.errors import problem
from fetcher.workers import (
    insert_job_row,
    new_batch_id,
    new_job_id,
    spawn_job,
    task_handles,
    update_job,
)


router = APIRouter()


class FetchItem(BaseModel):
    url: str
    quality: str = "fast"
    allow_paid: bool = False
    force_refresh: bool = False


class FetchBatch(BaseModel):
    requests: list[FetchItem]


def _is_valid_url_shape(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


@router.post("/v1/fetches")
async def post_fetches(batch: FetchBatch, request: Request) -> Response:
    settings = request.app.state.settings
    if len(batch.requests) > settings.batch_max:
        return problem(
            status=400,
            code="BATCH_TOO_LARGE",
            title=f"Batch exceeds FETCHER_BATCH_MAX={settings.batch_max}",
            detail=f"received {len(batch.requests)} items, max is {settings.batch_max}",
            instance="/v1/fetches",
            retryable=False,
        )

    conn = open_connection(settings.db_path)
    batch_id = new_batch_id()
    items: list[dict[str, Any]] = []
    try:
        for item in batch.requests:
            if not _is_valid_url_shape(item.url):
                items.append(
                    {
                        "error": {
                            "type": "https://fetcher/errors/bad-url",
                            "title": "Malformed URL",
                            "status": 400,
                            "code": "BAD_URL",
                            "detail": f"malformed URL: {item.url}",
                            "instance": "/v1/fetches",
                            "retryable": False,
                        }
                    }
                )
                continue
            job_id = new_job_id()
            request_body = item.model_dump()
            insert_job_row(conn, job_id=job_id, batch_id=batch_id, request_body=request_body)
            spawn_job(job_id, request_body, request)
            row = conn.execute("SELECT expires_at FROM fetches WHERE job_id = ?", (job_id,)).fetchone()
            items.append(
                {
                    "job_id": job_id,
                    "status_url": f"/v1/fetches/{job_id}",
                    "expires_at": row[0],
                }
            )
    finally:
        conn.close()

    return Response(
        status_code=202,
        content=json.dumps({"fetches": items}),
        media_type="application/json",
    )


@router.get("/v1/fetches/{job_id}")
async def get_fetch(job_id: str, request: Request) -> dict[str, Any]:
    conn = open_connection(request.app.state.settings.db_path)
    try:
        row = conn.execute(
            """
            SELECT job_id, status, created_at, updated_at, expires_at, result_json, error_json
              FROM fetches
             WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    body: dict[str, Any] = {
        "job_id": row[0],
        "status": row[1],
        "created_at": row[2],
        "updated_at": row[3],
    }
    if row[5]:
        body["result"] = json.loads(row[5])
    if row[6]:
        body["error"] = json.loads(row[6])
    return body


@router.delete("/v1/fetches/{job_id}")
async def delete_fetch(job_id: str, request: Request) -> Response:
    settings = request.app.state.settings
    conn = open_connection(settings.db_path)
    try:
        row = conn.execute("SELECT status FROM fetches WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="job not found")
        if row[0] in {"done", "failed"}:
            return problem(
                status=409,
                code="JOB_TERMINAL",
                title="Job is in a terminal state",
                detail=f"current status: {row[0]}",
                instance=f"/v1/fetches/{job_id}",
                retryable=False,
            )
    finally:
        conn.close()

    task = task_handles.get(job_id)
    if task is not None and not task.done():
        task.cancel()

    conn = open_connection(settings.db_path)
    try:
        row = conn.execute("SELECT status FROM fetches WHERE job_id = ?", (job_id,)).fetchone()
        if row and row[0] in {"pending", "running"}:
            update_job(
                conn,
                job_id=job_id,
                status="failed",
                error={
                    "code": "CANCELLED",
                    "title": "Job was cancelled",
                    "detail": "DELETE /v1/fetches/{job_id}",
                    "retryable": False,
                },
            )
    finally:
        conn.close()

    return Response(status_code=204)
