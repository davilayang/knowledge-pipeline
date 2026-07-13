"""Canonical Medium host identity — shared by kp's fetcher medium handler and
triage classification (`content_urls`).

The seed set is the well-known Medium-hosted publications; any `*.medium.com`
author subdomain also matches. Expand `MEDIUM_DOMAINS` from the actual
subscription set — it's the canonical core, not exhaustive.
"""

from urllib.parse import urlparse

# Hostnames whose articles are served by Medium's platform (bare host, no www —
# is_medium_url strips a leading www.). These also drive Medium-API parsing.
MEDIUM_DOMAINS = frozenset(
    {
        "ai.gopubby.com",
        "ai.plainenglish.io",
        "betterhumans.pub",
        "betterprogramming.pub",
        "blog.devgenius.io",
        "blog.stackademic.com",
        "gitconnected.com",
        "javascript.plainenglish.io",
        "levelup.gitconnected.com",
        "medium.com",
        "pub.towardsai.net",
        "python.plainenglish.io",
        "towardsdatascience.com",
        "uxdesign.cc",
    }
)


def is_medium_url(url: str) -> bool:
    """True if the URL is served by Medium — a known publication host or any
    `*.medium.com` author subdomain. Never raises; non-http(s) / malformed → False.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    host = host.removeprefix("www.")
    return host.endswith(".medium.com") or host in MEDIUM_DOMAINS
