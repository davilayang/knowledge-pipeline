"""Shared primitives for RapidAPI-backed extractors.

Each provider module composes these; none of them call a fat "do everything"
wrapper — the post-response handling diverges enough between providers
(medium2 vs facebook-scraper-api4 vs facebook-scraper3) that the right
shared surface is helpers, not orchestration.
"""

import httpx


def build_headers(host: str, api_key: str) -> dict[str, str]:
    """RapidAPI's two required headers, in the lowercase form NA + kp both use."""
    return {"x-rapidapi-key": api_key, "x-rapidapi-host": host}


def raise_for_status_with_body(response: httpx.Response, label: str) -> None:
    """Raise ValueError with the upstream body slice on HTTP ≥400.

    Matches kp's existing `rapidapi_medium.fetch_markdown` error shape so
    callers (and tier_log `detail` regex tests) keep working byte-for-byte
    after the relocation. The body slice is the bit that actually helps
    operators distinguish 'forbidden' from 'quota exhausted' from 'pfbid
    unknown' inside the same 4xx bucket.
    """
    if response.status_code >= 400:
        raise ValueError(f"{label} HTTP {response.status_code}: {response.text[:200]}")


def check_quota(response: httpx.Response, label: str) -> int:
    """Read `x-ratelimit-requests-remaining` and raise when exhausted.

    Returns the remaining count (defaults to 99 when the header is missing
    so a friendly provider doesn't break callers). Raises ValueError when
    the quota is 0 — caller maps to RawTierResult.detail just like any
    other upstream failure. Threshold-based warning is the caller's
    responsibility; this helper only enforces the hard zero.
    """
    try:
        remaining = int(response.headers.get("x-ratelimit-requests-remaining", "99"))
    except ValueError:
        remaining = 99
    if remaining == 0:
        raise ValueError(f"{label} quota exhausted (x-ratelimit-requests-remaining=0)")
    return remaining
