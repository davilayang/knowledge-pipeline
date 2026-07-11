"""Canonical source-metadata contract, shared producer→consumer.

The fetcher returns attribution metadata as a JSON dict; the knowledge-pipeline
consumer (`fetch_extract_queue`) reads a fixed set of keys off it. These key
names ARE the contract across that HTTP/JSON boundary — a handler hand-writing a
dict literal is how `author`-vs-`authors` drift slips in and silently drops a
field. Build every attribution metadata dict through `build_metadata` so the key
names live in exactly one place.

The keys mirror what the consumer reads (`assets.py`): `title`, `authors`,
`published` (content-published date), `arxiv_id`.
"""

from datetime import date, datetime
from typing import Any

TITLE = "title"
AUTHORS = "authors"
PUBLISHED = "published"
ARXIV_ID = "arxiv_id"


def _normalize_published(value: str | None) -> str | None:
    """Coerce a publish date to a plain `YYYY-MM-DD` string, or None if it can't be
    parsed. The consumer reads `published` with `date.fromisoformat`, which rejects
    a time component (`2026-06-29T00:00:00Z`) — so normalize at the source. An
    unparseable value (`March 1, 2026`) is dropped: a garbage date that crashes the
    consumer is worse than an absent one."""
    if not value:
        return None
    text = str(value).strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def build_metadata(
    *,
    title: str | None = None,
    authors: str | list[str] | None = None,
    published: str | None = None,
    arxiv_id: str | None = None,
) -> dict[str, Any]:
    """The canonical attribution metadata dict. Omits any key whose value is
    falsy, so 'absent' is a missing key rather than a null the consumer has to
    special-case — and a missing publish date stays absent, never a fake value."""
    out: dict[str, Any] = {}
    if title:
        out[TITLE] = title
    if authors:
        out[AUTHORS] = authors
    if normalized := _normalize_published(published):
        out[PUBLISHED] = normalized
    if arxiv_id:
        out[ARXIV_ID] = arxiv_id
    return out
