"""URL canonicalization: redirect-following plus tracking-param stripping."""

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx


_TRACKING_PARAM_PREFIXES = ("utm_", "_hsenc", "_hsmi", "mkt_tok")
_TRACKING_PARAMS_EXACT = frozenset(
    {
        "ref",
        "fbclid",
        "gclid",
        "msclkid",
        "yclid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "twclid",
    }
)


@dataclass(frozen=True)
class CanonicalResult:
    input_url: str
    canonical_url: str
    redirects_followed: list[str]
    params_stripped: list[str]
    # False when the redirect-follow failed and `canonical_url` is just the
    # input URL echoed back — indistinguishable from a URL that genuinely
    # redirects nowhere, so callers must not persist it.
    resolved: bool = True


def _is_tracking_param(name: str) -> bool:
    lowered = name.lower()
    return lowered in _TRACKING_PARAMS_EXACT or any(
        lowered.startswith(prefix) for prefix in _TRACKING_PARAM_PREFIXES
    )


def _strip_tracking_params(url: str) -> tuple[str, list[str]]:
    parsed = urlparse(url)
    if not parsed.query:
        return url, []

    kept: list[tuple[str, str]] = []
    stripped: list[str] = []
    for name, value in parse_qsl(parsed.query, keep_blank_values=True):
        if _is_tracking_param(name):
            stripped.append(name)
        else:
            kept.append((name, value))

    cleaned = urlunparse(parsed._replace(query=urlencode(kept, doseq=True)))
    return cleaned, stripped


def _follow_redirects(url: str, *, timeout_s: float = 10.0) -> httpx.Response:
    """Resolve the final URL using HEAD, falling back to streamed GET."""
    with httpx.Client(follow_redirects=True, timeout=timeout_s) as client:
        try:
            return client.head(url)
        except httpx.HTTPError:
            pass

        with client.stream("GET", url) as response:
            return response


def canonicalize(url: str, *, timeout_s: float = 10.0) -> CanonicalResult:
    """Return the canonical URL after following redirects and stripping trackers."""
    resolved = True
    try:
        response = _follow_redirects(url, timeout_s=timeout_s)
        final_url = str(response.url)
        redirects = [str(history.url) for history in (response.history or [])]
        if redirects:
            redirects.append(final_url)
    except httpx.HTTPError:
        final_url = url
        redirects = []
        resolved = False

    cleaned, stripped = _strip_tracking_params(final_url)
    return CanonicalResult(
        input_url=url,
        canonical_url=cleaned,
        redirects_followed=redirects,
        params_stripped=stripped,
        resolved=resolved,
    )
