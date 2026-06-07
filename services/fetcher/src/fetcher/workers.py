"""Background workers for async fetch jobs."""

import asyncio
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from fetcher.db import open_connection
from fetcher.endpoints.fetch import FetchRequest, run_fetch_request


logger = logging.getLogger(__name__)

task_handles: dict[str, asyncio.Task] = {}


def new_job_id() -> str:
    return "fetch_" + secrets.token_hex(8)


def new_batch_id() -> str:
    return "batch_" + secrets.token_hex(6)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _iso_plus_hours(hours: int) -> str:
    return (
        (datetime.now(timezone.utc) + timedelta(hours=hours))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def insert_job_row(conn, *, job_id: str, batch_id: str, request_body: dict[str, Any]) -> None:
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO fetches (
            job_id, status, request_json, batch_id, created_at, updated_at, expires_at
        )
        VALUES (?, 'pending', ?, ?, ?, ?, ?)
        """,
        (job_id, json.dumps(request_body), batch_id, now, now, _iso_plus_hours(24)),
    )


def update_job(
    conn,
    *,
    job_id: str,
    status: str,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        UPDATE fetches
           SET status = ?, result_json = ?, error_json = ?, updated_at = ?
         WHERE job_id = ?
        """,
        (
            status,
            json.dumps(result) if result else None,
            json.dumps(error) if error else None,
            _now_iso(),
            job_id,
        ),
    )


async def run_fetch_inline(req: dict[str, Any], *, request: Request) -> dict[str, Any]:
    response = await run_fetch_request(FetchRequest(**req), request)
    if not isinstance(response, JSONResponse):
        raise ValueError(f"unexpected fetch response status {response.status_code}")
    return json.loads(response.body)


async def _job_worker(job_id: str, req: dict[str, Any], request: Request) -> None:
    settings = request.app.state.settings
    conn = open_connection(settings.db_path)
    try:
        update_job(conn, job_id=job_id, status="running")
    finally:
        conn.close()

    try:
        result = await run_fetch_inline(req, request=request)
    except asyncio.CancelledError:
        conn = open_connection(settings.db_path)
        try:
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
            task_handles.pop(job_id, None)
        raise
    except Exception as exc:
        logger.warning("fetch job %s failed: %s", job_id, exc)
        conn = open_connection(settings.db_path)
        try:
            update_job(
                conn,
                job_id=job_id,
                status="failed",
                error={
                    "code": "UPSTREAM_FAILURE",
                    "title": "Fetch failed",
                    "detail": str(exc),
                    "retryable": True,
                },
            )
        finally:
            conn.close()
            task_handles.pop(job_id, None)
        return

    conn = open_connection(settings.db_path)
    try:
        update_job(conn, job_id=job_id, status="done", result=result)
    finally:
        conn.close()
        task_handles.pop(job_id, None)


def spawn_job(job_id: str, req: dict[str, Any], request: Request) -> None:
    task_handles[job_id] = asyncio.create_task(_job_worker(job_id, req, request))
