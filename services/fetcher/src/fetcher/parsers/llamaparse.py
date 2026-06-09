"""LlamaCloud REST wrapper for PDF URL to markdown."""

import asyncio
import time

import httpx


_LLAMACLOUD_BASE = "https://api.cloud.llamaindex.ai"


async def render_pdf(
    client: httpx.AsyncClient,
    *,
    pdf_url: str,
    api_key: str,
    tier: str,
    poll_interval_s: float = 2.0,
    poll_timeout_s: float = 180.0,
    base_url: str = _LLAMACLOUD_BASE,
) -> str:
    """Submit a PDF URL, poll until COMPLETED, and return markdown."""
    if not api_key:
        raise ValueError("LlamaParse api_key is unset")

    headers = {"Authorization": f"Bearer {api_key}"}
    submit = await client.post(
        f"{base_url}/api/v2/parse",
        headers=headers,
        json={"source_url": pdf_url, "tier": tier, "version": "latest"},
    )
    if submit.status_code >= 400:
        raise ValueError(f"LlamaParse submit HTTP {submit.status_code}: {submit.text[:200]}")

    job_id = (submit.json() or {}).get("id")
    if not job_id:
        raise ValueError("LlamaParse submit returned no job id")

    deadline = time.monotonic() + poll_timeout_s
    while time.monotonic() < deadline:
        poll = await client.get(
            f"{base_url}/api/v2/parse/{job_id}",
            params={"expand": "markdown_full"},
            headers=headers,
        )
        if poll.status_code >= 400:
            raise ValueError(f"LlamaParse poll HTTP {poll.status_code} for job {job_id}")

        body = poll.json() or {}
        status = (body.get("job") or {}).get("status") or body.get("status")
        if status == "COMPLETED":
            markdown = body.get("markdown_full") or body.get("markdown")
            if not markdown:
                result = await client.get(
                    f"{base_url}/api/v2/parse/{job_id}/result/markdown",
                    headers=headers,
                )
                markdown = (result.json() or {}).get("markdown", "")
            if not markdown:
                raise ValueError(f"LlamaParse job {job_id} completed with empty markdown")
            return markdown
        if status in {"FAILED", "CANCELLED"}:
            err = (body.get("job") or {}).get("error_message") or body.get("error") or status
            raise ValueError(f"LlamaParse job {job_id} {status}: {err}")
        await asyncio.sleep(poll_interval_s)

    raise ValueError(f"LlamaParse polling timed out for job {job_id} after {poll_timeout_s}s")
