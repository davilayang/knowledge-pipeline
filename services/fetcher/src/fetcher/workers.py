"""Background workers for async fetch jobs."""

import asyncio
import logging
import secrets
from pathlib import Path
from typing import Any

from domains.fetches_store.sources import update_job
from fetcher import task_registry
from fetcher.config import Settings
from fetcher.fetch_service import run_fetch_request
from fetcher.problems import problem_body
from fetcher.types import FetchContext, FetchRequest, TierLogEntry


logger = logging.getLogger(__name__)


def new_job_id() -> str:
    return "fetch_" + secrets.token_hex(8)


def new_batch_id() -> str:
    return "batch_" + secrets.token_hex(6)


def _tier_log_payload(tier_log: list[TierLogEntry]) -> list[dict[str, Any]]:
    return [
        {
            "tier": entry.tier,
            "status": entry.status,
            "chars": entry.chars,
            "error": entry.error,
            "validated": entry.validated,
        }
        for entry in tier_log
    ]


async def _job_worker(
    job_id: str,
    req: dict[str, Any],
    *,
    settings: Settings,
    ctx: FetchContext,
) -> None:
    db_path = Path(settings.db_path)
    update_job(db_path=db_path, job_id=job_id, status="running")

    try:
        fetch_req = FetchRequest(**req)
        outcome = await run_fetch_request(
            fetch_req,
            db_path=db_path,
            ctx=ctx,
            ttl_days=settings.cache_ttl_days,
        )

        result = {
            "markdown": outcome.markdown,
            "source_type": outcome.source_type,
            "canonical_url": outcome.canonical_url,
            "tier_used": outcome.tier_used,
            "fetched_at": outcome.fetched_at,
            "cache_hit": outcome.cache_hit,
            "etag": outcome.etag,
            "tier_log": _tier_log_payload(outcome.tier_log),
            "metadata": outcome.metadata or {},
        }
        update_job(db_path=db_path, job_id=job_id, status="done", result=result)

    except asyncio.CancelledError:
        # Check current status: if already done/failed, don't overwrite
        # This prevents the "just finished" vs "just cancelled" race.
        from domains.fetches_store.sources import get_job_status

        status = get_job_status(db_path=db_path, job_id=job_id)
        if status in {"pending", "running"}:
            error = problem_body(
                status=499,  # Client Closed Request-ish
                code="CANCELLED",
                title="Job was cancelled",
                detail="DELETE /v1/fetches/{job_id}",
                instance=f"/v1/fetches/{job_id}",
                retryable=False,
            )
            update_job(db_path=db_path, job_id=job_id, status="failed", error=error)
        raise
    except Exception as exc:
        logger.warning("fetch job %s failed: %s", job_id, exc)
        # Map domain exceptions to problem bodies if possible
        status_code = 502
        code = "UPSTREAM_FAILURE"
        title = "Fetch failed"
        retryable = True

        if hasattr(exc, "status"):
            status_code = exc.status
        if hasattr(exc, "code"):
            code = exc.code
        if hasattr(exc, "title"):
            title = exc.title
        if hasattr(exc, "retryable"):
            retryable = exc.retryable

        error = problem_body(
            status=status_code,
            code=code,
            title=title,
            detail=str(exc),
            instance=f"/v1/fetches/{job_id}",
            retryable=retryable,
            canonical_url=getattr(exc, "extra", {}).get("canonical_url"),
            tier_log=_tier_log_payload(getattr(exc, "extra", {}).get("tier_log", [])),
        )
        update_job(db_path=db_path, job_id=job_id, status="failed", error=error)
    finally:
        task_registry.discard(job_id)


def spawn_job(
    job_id: str,
    req: dict[str, Any],
    *,
    settings: Settings,
    ctx: FetchContext,
) -> None:
    task = asyncio.create_task(_job_worker(job_id, req, settings=settings, ctx=ctx))
    task_registry.register(job_id, task)
