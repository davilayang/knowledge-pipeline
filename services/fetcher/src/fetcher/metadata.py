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

from typing import Any

TITLE = "title"
AUTHORS = "authors"
PUBLISHED = "published"
ARXIV_ID = "arxiv_id"


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
    if published:
        out[PUBLISHED] = published
    if arxiv_id:
        out[ARXIV_ID] = arxiv_id
    return out
