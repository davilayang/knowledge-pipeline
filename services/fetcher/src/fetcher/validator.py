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


# An authentication demand wherever it appears, including inside a link or
# button — a body ending in one is walled, not merely linking onward.
_AUTH_MARKERS = (
    "log in to continue",
    "log in to see more",
    "log in or sign up",
)

# Ambiguous with site navigation: a paywall says it, and so does a footer link
# to the next post. Only counted as truncation outside a markdown link.
_NAV_AMBIGUOUS_MARKERS = (
    "see more",
    "continue reading",
    "read the full article",
)

_TRUNCATION_MARKERS = _AUTH_MARKERS + _NAV_AMBIGUOUS_MARKERS


# A markdown link's visible text: navigation ("[Continue reading...](/other-post)"),
# not this body being cut off. Stripped before truncation markers are matched.
_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")

# How much of the body's end is scanned for truncation markers. A genuine
# cut-off sits at the end; a "see more" inside real prose earlier in the body
# does not mean the body is incomplete.
_TAIL_SCAN_CHARS = 1000

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
    body_end = md.rstrip()
    for line in body_end[-_TAIL_SCAN_CHARS:].splitlines():
        lower = line.lower()
        if any(marker in lower for marker in _AUTH_MARKERS):
            return True
        bare = _MARKDOWN_LINK.sub(" ", lower)
        if any(marker in bare for marker in _NAV_AMBIGUOUS_MARKERS):
            return True
    tail = body_end[-200:]
    if _ELLIPSIS_TAIL.search(tail):
        return True
    if len(md) < 500:
        return False
    return not _TERMINAL_PUNCT.search(tail)


def is_acceptable(md: str) -> bool:
    """Tier-validator gate: content is clean and complete."""
    return is_valid_content(md) and not is_likely_truncated(md)
