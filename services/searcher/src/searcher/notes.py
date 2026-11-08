"""The notes lane: newsletter-assistant's saved notes, found through Chroma's
`notes` collection and resolved in the note files (hub contract
`contracts/note-brief-files.md`)."""

import re
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml
from retrievers.vector_store.chroma import COLLECTION_NOTES

from searcher.engine import Hit, LaneFn, dense, lane, timed
from searcher.frontmatter import read_header
from searcher.models import Item, SearchRequest

# newsletter-assistant's note naming, `<date>_<slug>-<6 hex>` (`core.notes`).
_STEM = re.compile(r"^(\d{4}-\d{2}-\d{2})_([a-z0-9]+(?:-[a-z0-9]+)*)-([a-f0-9]{6})$")


def notes_lane(chroma: Callable[[], Awaitable[Any]], notes_dir: Path) -> LaneFn:
    def resolve(note_ids: list[str]) -> dict[str, dict[str, Any]]:
        with timed("note files"):
            # Every hit would drop with no directory, reading as "nothing matched".
            if not notes_dir.is_dir():
                raise FileNotFoundError(f"no notes directory at {notes_dir}")
            return {i: fm for i in note_ids if (fm := _header(notes_dir, i)) is not None}

    return lane("notes", {"dense": dense(chroma, COLLECTION_NOTES, _note_id)}, resolve, _item)


def _note_id(meta: dict[str, Any]) -> str:
    """The indexer keys a chunk by `local:<file>`; the note id is the file's stem."""
    ref = str(meta.get("source_ref", ""))
    return ref.removeprefix("local:").removesuffix(".md") if ref.startswith("local:") else ""


def _header(notes_dir: Path, note_id: str) -> dict[str, Any] | None:
    """What the item needs from the note, or None where newsletter-assistant cannot open the note:
    a stem outside its naming (the indexer embeds any `*.md`; the pattern also
    keeps an id from reaching outside the directory), a missing file, a header
    that does not parse, or a `note_id` that is not the stem. A note saved
    before the `note_id` field existed has none, and its stem stands."""
    if _STEM.match(note_id) is None:
        return None
    try:
        # newsletter-assistant dates a note by its stem, not the header `date`.
        day = date.fromisoformat(note_id[:10])
        fm = read_header(notes_dir / f"{note_id}.md")
    except (OSError, ValueError, yaml.YAMLError):
        return None
    if "note_id" in fm and fm["note_id"] != note_id:
        return None
    return {"date": day}


def _item(req: SearchRequest, note_id: str, note: dict[str, Any], hit: Hit) -> Item | None:
    if req.since is not None and note["date"] < req.since:
        return None
    return Item(
        uri=f"kos://piece/note/{quote(note_id, safe=':')}",
        kind="note",
        # The title as indexed; the indexer stores no title for a blank one, and
        # the contract shows that note by its stem.
        title=str(hit.meta.get("title") or "") or note_id,
        author=None,
        date=note["date"],
        url=None,
        summary=None,
        passages=[hit.chunk],
        meta={},
        matched_by=[],
    )
