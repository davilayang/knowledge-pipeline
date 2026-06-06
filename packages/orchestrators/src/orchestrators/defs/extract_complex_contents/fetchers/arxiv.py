"""arXiv fetcher — metadata via the `arxiv` PyPI client, PDF → markdown via LlamaParse.

kp uses LlamaParse (LlamaCloud) on the `agentic_plus` tier for arxiv PDF
rendering. The async ingestion layer accepts the ~60s/26-page latency
cost in exchange for higher-quality markdown than pymupdf4llm produces.
NA's equivalent fetcher uses pymupdf4llm (latency matters there).

Module named `arxiv` here (not `arxiv_fetcher`) because kp imports it as
`from .fetchers import arxiv as arxiv_fetcher` to avoid shadowing the PyPI package
at call sites.
"""

import logging
import re
import threading
import time
from urllib.parse import urlparse

import arxiv
import httpx
import requests
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_delay,
    wait_exponential_jitter,
)

from .result import FetchResult

logger = logging.getLogger(__name__)

# Serialise arxiv calls process-wide so concurrent asset runs don't amplify
# each other into worse 429 rate-limiting.
_ARXIV_SEMAPHORE = threading.Semaphore(1)

_RETRY_STOP_AFTER_SECONDS = 15
_RETRY_WAIT_INITIAL = 2
_RETRY_WAIT_MAX = 8

_ARXIV_HOSTS = ("arxiv.org", "www.arxiv.org", "export.arxiv.org")

# New-style ID: 4 digits + '.' + 4-5 digits, optional version (vN).
# Old-style ID: archive/subject-class slug + '/' + 7 digits, optional version.
_NEW_ID_RE = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
_OLD_ID_RE = re.compile(r"^([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?$")

_REQUEST_TIMEOUT_S = 60.0

# LlamaParse poll cadence + ceiling. NA's `pdf_extract.py` constants —
# 2s poll matches the typical ~3–4s `cost_effective` completion; 180s
# total handles a 26-page paper on `agentic_plus` with headroom. kp uses
# `agentic_plus` for arxiv (quality > speed; no real-time user waiting).
_LLAMAPARSE_POLL_INTERVAL_S = 2.0
_LLAMAPARSE_POLL_TIMEOUT_S = 180.0


class _NoArxivRecord(Exception):
    """Sentinel — `arxiv.Client.results()` returned an empty iterator.

    Distinguishes a permanent 'no record' result from a retryable HTTPError so
    the tenacity loop doesn't retry on it.
    """


def is_arxiv_url(url: str) -> bool:
    """Return True if ``url`` points at an arXiv abstract or PDF page."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    if host not in _ARXIV_HOSTS:
        return False
    path = parsed.path.strip("/")
    if not path:
        return False
    if path.startswith("abs/") or path.startswith("pdf/"):
        return _looks_like_id(_strip_pdf_suffix(path.split("/", 1)[1]))
    return _looks_like_id(_strip_pdf_suffix(path))


def _strip_pdf_suffix(s: str) -> str:
    return s[:-4] if s.endswith(".pdf") else s


def _looks_like_id(candidate: str) -> bool:
    return bool(_NEW_ID_RE.match(candidate) or _OLD_ID_RE.match(candidate))


def extract_arxiv_id(url: str) -> str:
    """Extract the canonical (version-stripped) arXiv ID from a URL.

    Raises ``ValueError`` if the URL has no recognisable ID.
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if path.startswith(("abs/", "pdf/")):
        path = path.split("/", 1)[1]
    path = _strip_pdf_suffix(path)

    m = _NEW_ID_RE.match(path) or _OLD_ID_RE.match(path)
    if not m:
        raise ValueError(f"not a recognisable arXiv ID in URL: {url!r}")
    return m.group(1)


def _format_content(
    *,
    arxiv_id: str,
    title: str,
    authors: list[str],
    published: str,
    primary_category: str,
    categories: list[str],
    abstract: str,
    body_md: str,
) -> str:
    other = [c for c in categories if c != primary_category]
    cat_line = primary_category
    if other:
        cat_line += f" ({', '.join(other)})"

    return (
        f"# {title}\n\n"
        f"**Authors:** {', '.join(authors)}\n"
        f"**Published:** {published}\n"
        f"**Categories:** {cat_line}\n"
        f"**arXiv:** {arxiv_id}\n\n"
        f"## Abstract\n\n"
        f"{abstract.strip()}\n\n"
        f"---\n\n"
        f"{body_md.strip()}\n"
    )


def _poll_llamaparse_job(
    client: httpx.Client,
    job_id: str,
    *,
    base_url: str,
    api_key: str,
    max_seconds: float,
    interval: float,
) -> str:
    """Poll a LlamaParse job until COMPLETED. Raises on FAILED / CANCELLED /
    HTTP error / timeout. Returns the markdown_full string on success."""
    url = f"{base_url}/api/v2/parse/{job_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    deadline = time.monotonic() + max_seconds
    while time.monotonic() < deadline:
        resp = client.get(url, params={"expand": "markdown_full"}, headers=headers)
        if resp.status_code >= 400:
            raise ValueError(f"LlamaParse poll HTTP {resp.status_code} for job {job_id}")
        payload = resp.json()
        status = (payload.get("job") or {}).get("status")
        if status == "COMPLETED":
            md = payload.get("markdown_full") or ""
            if not isinstance(md, str) or not md:
                raise ValueError(f"LlamaParse job {job_id} completed with empty markdown")
            return md
        if status in ("FAILED", "CANCELLED"):
            err = (payload.get("job") or {}).get("error_message", "")
            raise ValueError(f"LlamaParse job {job_id} {status}: {err}")
        time.sleep(interval)
    raise ValueError(f"LlamaParse polling timed out for job {job_id} after {max_seconds}s")


def _llamaparse_to_markdown(
    pdf_url: str,
    *,
    api_key: str,
    base_url: str,
    tier: str,
) -> str:
    """Submit a LlamaParse job for the PDF URL, poll until COMPLETED, return
    markdown. Hard-fail: raises ValueError on any failure (no fallback to
    pymupdf4llm). kp accepts the latency cost in exchange for quality — the
    async ingestion layer is not user-facing."""
    if not api_key:
        raise ValueError("LlamaParse api_key is unset; cannot render arxiv PDFs")
    headers = {"Authorization": f"Bearer {api_key}"}
    with httpx.Client(timeout=_REQUEST_TIMEOUT_S) as client:
        resp = client.post(
            f"{base_url}/api/v2/parse",
            headers=headers,
            json={"source_url": pdf_url, "tier": tier, "version": "latest"},
        )
        if resp.status_code >= 400:
            raise ValueError(f"LlamaParse submit HTTP {resp.status_code}: {resp.text[:200]}")
        job_id = (resp.json() or {}).get("id")
        if not job_id:
            raise ValueError("LlamaParse submit returned no job id")
        return _poll_llamaparse_job(
            client,
            job_id,
            base_url=base_url,
            api_key=api_key,
            max_seconds=_LLAMAPARSE_POLL_TIMEOUT_S,
            interval=_LLAMAPARSE_POLL_INTERVAL_S,
        )


def fetch(
    url: str,
    *,
    llama_cloud_api_key: str,
    llama_cloud_base_url: str,
    llama_parse_tier: str,
) -> FetchResult:
    """Fetch arXiv paper as markdown (metadata header + LlamaParse-rendered
    PDF body).

    The arxiv-rate-limited metadata call is serialised process-wide via
    ``_ARXIV_SEMAPHORE``; LlamaParse runs outside the semaphore.

    Hard-fails on any LlamaParse failure (no pymupdf4llm fallback). kp's
    arxiv path prioritises quality over latency since the agent layer
    (newsletter-assistant) doesn't wait on extraction — the row sits in
    Notion Status=Fetching until extract completes.
    """
    return _fetch_inner(
        url,
        llama_cloud_api_key=llama_cloud_api_key,
        llama_cloud_base_url=llama_cloud_base_url,
        llama_parse_tier=llama_parse_tier,
    )


def _fetch_inner(
    url: str,
    *,
    llama_cloud_api_key: str,
    llama_cloud_base_url: str,
    llama_parse_tier: str,
) -> FetchResult:
    attempts = 0

    try:
        arxiv_id = extract_arxiv_id(url)
    except ValueError as exc:
        logger.info("arxiv tier: permanent error for %s — %s", url, exc)
        return FetchResult(error=str(exc))

    client = arxiv.Client(num_retries=1, delay_seconds=3)

    def _attempt_metadata() -> "arxiv.Result":
        nonlocal attempts
        attempts += 1
        results = client.results(arxiv.Search(id_list=[arxiv_id]))
        try:
            return next(results)
        except StopIteration:
            raise _NoArxivRecord(arxiv_id)

    t_retry_start = time.monotonic()
    with _ARXIV_SEMAPHORE:
        try:
            for attempt in Retrying(
                retry=retry_if_exception_type(
                    (arxiv.HTTPError, requests.exceptions.ConnectionError)
                ),
                stop=stop_after_delay(_RETRY_STOP_AFTER_SECONDS),
                wait=wait_exponential_jitter(initial=_RETRY_WAIT_INITIAL, max=_RETRY_WAIT_MAX),
                reraise=True,
            ):
                with attempt:
                    paper = _attempt_metadata()
        except _NoArxivRecord:
            retry_ms = int((time.monotonic() - t_retry_start) * 1000)
            logger.warning(
                "arxiv tier: no results for id %s (attempts=%d, wait_ms=%d)",
                arxiv_id,
                attempts,
                retry_ms,
            )
            return FetchResult(error=f"no arXiv record for {arxiv_id}")
        except (arxiv.HTTPError, requests.exceptions.ConnectionError) as exc:
            retry_ms = int((time.monotonic() - t_retry_start) * 1000)
            reason = f"{type(exc).__name__}: {exc}"[:300]
            logger.error(
                "arxiv tier: retry budget exhausted for %s — %s (attempts=%d, wait_ms=%d)",
                url,
                reason,
                attempts,
                retry_ms,
            )
            return FetchResult(error=reason)

    title = paper.title.strip()
    authors = [a.name for a in paper.authors]
    abstract = paper.summary.strip()
    published = paper.published.date().isoformat() if paper.published else ""
    primary_category = paper.primary_category or ""
    categories = list(paper.categories or [])
    pdf_url = paper.pdf_url
    if not pdf_url:
        return FetchResult(error=f"no pdf_url for {arxiv_id}")

    try:
        body_md = _llamaparse_to_markdown(
            pdf_url,
            api_key=llama_cloud_api_key,
            base_url=llama_cloud_base_url,
            tier=llama_parse_tier,
        )
    except (ValueError, httpx.HTTPError) as exc:
        return FetchResult(error=f"{type(exc).__name__}: {exc}"[:300])

    content = _format_content(
        arxiv_id=arxiv_id,
        title=title,
        authors=authors,
        published=published,
        primary_category=primary_category,
        categories=categories,
        abstract=abstract,
        body_md=body_md,
    )
    return FetchResult(
        content=content,
        tier="arxiv",
        tier_log=[
            {
                "tier": "arxiv",
                "status": "ok",
                "chars": len(content),
                "llamaparse_tier": llama_parse_tier,
                "renderer": "llamaparse",
            }
        ],
        title=title,
        author=", ".join(authors),
        extras={
            "arxiv_id": arxiv_id,
            "title": title,
            "authors": authors,
            "published": published,
            "primary_category": primary_category,
            "categories": categories,
        },
    )
