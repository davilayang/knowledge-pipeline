"""GitHub handler: fetch a repo's README.md from the raw host.

A `github.com/<org>/<repo>` URL → `raw.githubusercontent.com/<org>/<repo>/HEAD/README.md`
(HEAD resolves the default branch, dodging main-vs-master). Minimal by design:
only the canonical `README.md` at the repo root is fetched. Anything else — a repo
with no README.md (or a non-`.md` README), a private repo, or a deeper URL like
`/blob/…` or `/tree/…` — fails the tier, and the item falls to the error-state for
the user to paste the body manually. Keyless: no GitHub API / token, and no Jina
(whose anonymous tier is abuse-blocked for github.com).
"""

import logging
from urllib.parse import urlparse

from fetcher.types import FetchContext, RawTierResult, Tier

logger = logging.getLogger(__name__)

NAME = "github"
STRICT_PAID_TIER = False

_RAW_BASE = "https://raw.githubusercontent.com"


def _owner_repo(url: str) -> tuple[str, str] | None:
    """`(owner, repo)` for a `github.com/<org>/<repo>` root URL, else None.

    Requires the host to be exactly `github.com` (not gist./docs./api.) and the
    path to be exactly two segments — so repo subpages (`/blob/…`, `/tree/…`) and
    GitHub-hosted `.pdf` files fall through to the pdf/article handlers instead.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host != "github.com":
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def matches(url: str) -> bool:
    return _owner_repo(url) is not None


async def _readme_fetch(ctx: FetchContext, url: str) -> RawTierResult:
    owner_repo = _owner_repo(url)
    if owner_repo is None:
        return RawTierResult(content="", status=0, detail=f"not a github repo URL: {url!r}")
    owner, repo = owner_repo
    raw_url = f"{_RAW_BASE}/{owner}/{repo}/HEAD/README.md"
    try:
        resp = await ctx.http_client.get(
            raw_url, follow_redirects=True, timeout=ctx.upstream_timeout_s
        )
    except Exception as exc:
        logger.warning("github README fetch failed for %s/%s: %s", owner, repo, exc)
        return RawTierResult(
            content="", status=0, detail=f"github readme fetch failed: {type(exc).__name__}: {exc}"
        )
    if resp.status_code >= 400:
        # No README.md at HEAD (or private/nonexistent) — fail the tier so the
        # item hits the error-state and the user pastes the body.
        return RawTierResult(
            content="",
            status=resp.status_code,
            detail=f"github README.md HTTP {resp.status_code} for {owner}/{repo}",
        )
    return RawTierResult(content=resp.text, status=resp.status_code)


TIERS: list[Tier] = [
    Tier("github_readme", "free", 500, 2000, _readme_fetch),
]
