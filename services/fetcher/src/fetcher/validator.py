"""Content validator: detects block pages, paywalls, and truncated bodies.

A handler's tier `validate` callback wraps `is_acceptable` so the cascade
treats partial/blocked content as a tier failure rather than a soft success.
"""

import re


MIN_CONTENT_CHARS = 1000


_BLOCK_MARKERS = (
    # JavaScript walls
    "please enable javascript",
    "you need to enable javascript",
    "enable javascript and cookies",
    # Cloudflare / generic security challenges
    "security verification",
    "performing security verification",
    "checking if the site connection is secure",
    "cloudflare ray id",
    # Generic not-found / error pages
    "page not found",
    # Medium paywall markers
    "this story is only available to medium members",
    "become a member to read this story",
    "read the rest of this story with a free account",
    "get access to this story",
    "create an account to read the full story",
    "member-only story",
)


_TRUNCATION_MARKERS = (
    "see more",
    "continue reading",
    "read the full article",
    "log in to continue",
    "log in to see more",
    "log in or sign up",
)


_ELLIPSIS_TAIL = re.compile(r"(\.\.\.|…)\s*$")
_TERMINAL_PUNCT = re.compile(r"[.!?]")


def is_valid_content(md: str) -> bool:
    """True if the markdown looks like real article content (not a block page)."""
    if len(md.strip()) < MIN_CONTENT_CHARS:
        return False
    lower = md.lower()
    return not any(marker in lower for marker in _BLOCK_MARKERS)


def is_likely_truncated(md: str) -> bool:
    """True if the body is cut off (paywalled, login-walled, or "see more")."""
    if not md:
        return False
    lower = md.lower()
    if any(marker in lower for marker in _TRUNCATION_MARKERS):
        return True
    tail = md.rstrip()[-200:]
    if _ELLIPSIS_TAIL.search(tail):
        return True
    if len(md) < 500:
        return False
    return not _TERMINAL_PUNCT.search(tail)


def is_acceptable(md: str) -> bool:
    """Tier-validator gate: content is clean and complete."""
    return is_valid_content(md) and not is_likely_truncated(md)
