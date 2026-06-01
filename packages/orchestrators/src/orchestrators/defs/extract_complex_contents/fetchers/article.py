"""Article fetcher — Jina → curl_cffi + trafilatura cascade."""

import logging
from typing import Any

from .result import FetchResult

logger = logging.getLogger(__name__)


def fetch(
    url: str,
    *,
    pi_socks5_url: str,
    impersonate_profile: str = "safari17_0",
    jina_floor_chars: int = 2000,
    timeout_s: int = 30,
) -> FetchResult:
    tier_log: list[dict[str, Any]] = []

    jina_content, jina_status, jina_error = _jina_fetch(url, timeout_s=timeout_s)
    tier_log.append(
        {
            "tier": "jina",
            "status": jina_status,
            "chars": len(jina_content),
            "error": jina_error,
        }
    )
    if jina_content and len(jina_content) >= jina_floor_chars:
        return FetchResult(content=jina_content, tier="jina", tier_log=tier_log)

    html, curl_status, curl_error = _curl_cffi_fetch(
        url,
        proxy=pi_socks5_url,
        impersonate=impersonate_profile,
        timeout_s=timeout_s,
    )
    markdown = _trafilatura_extract(html) if html else ""
    tier_log.append(
        {
            "tier": "curl_cffi",
            "status": curl_status,
            "chars": len(markdown),
            "error": curl_error,
        }
    )
    return FetchResult(content=markdown, tier="curl_cffi", tier_log=tier_log)


def _jina_fetch(url: str, *, timeout_s: int) -> tuple[str, int | None, str | None]:
    """Pull markdown via r.jina.ai. Returns (content, http_status, error)."""
    import requests

    try:
        resp = requests.get(f"https://r.jina.ai/{url}", timeout=timeout_s)
        return resp.text or "", resp.status_code, None
    except Exception as exc:  # pragma: no cover — exercised via monkeypatch in tests
        return "", None, str(exc)


def _curl_cffi_fetch(
    url: str, *, proxy: str, impersonate: str, timeout_s: int
) -> tuple[str, int | None, str | None]:
    """Fetch through Pi SOCKS5 with browser-impersonating TLS fingerprint."""
    from curl_cffi import requests as curl_requests

    try:
        resp = curl_requests.get(
            url,
            impersonate=impersonate,
            proxies={"http": proxy, "https": proxy},
            timeout=timeout_s,
        )
        return resp.text or "", resp.status_code, None
    except Exception as exc:  # pragma: no cover — exercised via monkeypatch in tests
        return "", None, str(exc)


def _trafilatura_extract(html: str) -> str:
    import trafilatura

    return (
        trafilatura.extract(
            html,
            output_format="markdown",
            include_links=True,
            include_tables=True,
        )
        or ""
    )
