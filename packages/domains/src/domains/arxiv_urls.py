"""Canonical arXiv URL identity — the single source of arXiv ID regexes + host set.

Shared by kp's fetcher (`handlers/arxiv.py`, `handlers/pdf.py`) and triage
(`triage_knowledge_queue/classify.py`), which previously each kept a byte-for-byte
copy. NA keeps its own copy (separate repo). Update the regexes here on any arXiv
ID-format change; kp's two consumers then stay in lockstep automatically.
"""

import re
from urllib.parse import urlparse

ARXIV_HOSTS = ("arxiv.org", "www.arxiv.org", "export.arxiv.org")
ARXIV_PATH_PREFIXES = ("abs/", "pdf/", "html/")
# group(1) returns the version-stripped canonical ID; the optional trailing
# `(v\d+)?` matches (and discards) the version suffix.
NEW_ID_RE = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
OLD_ID_RE = re.compile(r"^([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?$")


def is_arxiv_id(candidate: str) -> bool:
    """True if `candidate` is a bare arXiv ID (new `YYMM.NNNNN` or old `cat/NNNNNNN`)."""
    return bool(NEW_ID_RE.match(candidate) or OLD_ID_RE.match(candidate))


def extract_arxiv_id(url: str) -> str | None:
    """Version-stripped canonical arXiv ID from a URL, or None if it isn't one.

    Handles the abs/pdf/html path prefixes and a trailing `.pdf`. Returns None
    when the host isn't an arXiv host or the path holds no recognisable ID.
    """
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        # urlparse / .hostname raises on malformed input (e.g. bad IPv6) — a
        # "is this arXiv" query should answer "no", not blow up on a stray URL.
        return None
    if host not in ARXIV_HOSTS:
        return None
    path = parsed.path.strip("/")
    if path.startswith(ARXIV_PATH_PREFIXES):
        path = path.split("/", 1)[1]
    if path.endswith(".pdf"):
        path = path[:-4]
    match = NEW_ID_RE.match(path) or OLD_ID_RE.match(path)
    return match.group(1) if match else None


def is_arxiv_url(url: str) -> bool:
    """True if the URL is an arXiv host carrying a recognisable arXiv ID path."""
    return extract_arxiv_id(url) is not None
