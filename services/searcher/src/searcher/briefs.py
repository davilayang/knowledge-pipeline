"""The briefs lane: newsletter-assistant's briefs, found through Chroma's
`briefings` collection and resolved in the brief files (hub contract
`contracts/note-brief-files.md`)."""

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml
from retrievers.vector_store.chroma import COLLECTION_BRIEFINGS

from searcher.engine import Hit, LaneFn, dense, lane, timed
from searcher.frontmatter import read_header
from searcher.models import Item, SearchRequest

# The header keys newsletter-assistant needs to read a brief (`core.briefs`).
_REQUIRED = {"brief_id", "slug", "title", "status", "question", "drafted_at"}


def briefs_lane(chroma: Callable[[], Awaitable[Any]], briefs_dir: Path) -> LaneFn:
    # The index names a brief by its file, so pieces are keyed by file name; the
    # uri uses the `brief_id` read from the file.
    def resolve(names: list[str]) -> dict[str, dict[str, Any]]:
        with timed("brief files"):
            # Every hit would drop with no directory, reading as "nothing matched".
            if not briefs_dir.is_dir():
                raise FileNotFoundError(f"no briefs directory at {briefs_dir}")
            return {n: fm for n in names if (fm := _header(briefs_dir, n)) is not None}

    return lane("briefs", {"dense": dense(chroma, COLLECTION_BRIEFINGS, _file)}, resolve, _item)


def _file(meta: dict[str, Any]) -> str:
    ref = str(meta.get("source_ref", ""))
    return ref.removeprefix("local:") if ref.startswith("local:") else ""


def _header(briefs_dir: Path, name: str) -> dict[str, Any] | None:
    """The brief's header, or None where newsletter-assistant cannot read the
    brief: a name that is not a flat `*.md` in the directory (it lists only
    those, which also keeps a name from reaching outside it), a missing file, a
    header that does not parse, a required key missing, or an unreadable
    `drafted_at`."""
    if Path(name).name != name or not name.endswith(".md"):
        return None
    try:
        fm = read_header(briefs_dir / name)
        # The uri is built from `brief_id`, so a blank or non-string one cannot name the brief.
        if not _REQUIRED <= fm.keys() or not isinstance(fm["brief_id"], str) or not fm["brief_id"]:
            return None
        drafted = fm["drafted_at"]
        if isinstance(drafted, str):
            drafted = datetime.fromisoformat(drafted)
        # An unquoted `drafted_at: 2026-10-02` loads as a date, which
        # newsletter-assistant keeps; it is taken as midnight UTC.
        if isinstance(drafted, date) and not isinstance(drafted, datetime):
            drafted = datetime(drafted.year, drafted.month, drafted.day)
        if not isinstance(drafted, datetime):
            return None
        # Contract §3: dates are UTC dates; newsletter-assistant takes a naive one as UTC.
        drafted = drafted.replace(tzinfo=UTC) if drafted.tzinfo is None else drafted.astimezone(UTC)
    except (OSError, ValueError, OverflowError, yaml.YAMLError):
        return None
    return {**fm, "drafted": drafted}


def _item(req: SearchRequest, name: str, fm: dict[str, Any], hit: Hit) -> Item | None:
    if req.since is not None and fm["drafted"].date() < req.since:
        return None
    return Item(
        uri=f"kos://piece/brief/{quote(str(fm['brief_id']), safe=':')}",
        kind="brief",
        # The contract shows a brief with a blank title by its file name, no `.md`.
        title=str(fm["title"] or "") or name.removesuffix(".md"),
        author=None,
        date=fm["drafted"].date(),
        url=None,
        summary=None,
        passages=[hit.chunk],
        meta={"status": fm["status"]},
        matched_by=[],
    )
