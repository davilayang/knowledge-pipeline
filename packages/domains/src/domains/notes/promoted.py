"""Frontmatter-aware reader for user-promoted notes (KP-2).

`LocalFileSource` (sources.py) strips YAML frontmatter and surfaces only
title/date. Note→wiki promotion needs the fields it discards — `promote`,
`entities` (relevance-ordered hints), `updated_at`, `session_id` — plus a
stable `note_id`. NA ships no `note_id:` field, so it is the filename stem
(the stem is minted once at save and never renamed, so it survives edits).
Returns only notes flagged `promote: true`.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from domains.notes.sources import _parse_date_prefix, _strip_frontmatter


@dataclass(frozen=True)
class PromotedNote:
    """One user-promoted note. `entities` are best-effort, relevance-ordered
    entity-name hints (most→least); resolution against wiki.db happens in the
    promote_notes workflow, not here."""

    note_id: str  # filename stem — stable identity across title/body edits
    title: str
    date: date | None
    body: str
    entities: tuple[str, ...]
    updated_at: str | None
    session_id: str | None


def read_promoted_notes(notes_dir: Path) -> list[PromotedNote]:
    """Read every `promote: true` note file in `notes_dir` (flat `*.md`)."""
    if not notes_dir.exists():
        return []

    out: list[PromotedNote] = []
    for path in sorted(notes_dir.glob("*.md")):
        body, meta = _strip_frontmatter(path.read_text(encoding="utf-8"))
        if meta.get("promote") is not True:
            continue

        file_date = meta.get("date")
        if isinstance(file_date, str):
            file_date = date.fromisoformat(file_date)
        elif file_date is None:
            file_date = _parse_date_prefix(path.stem)

        entities = meta.get("entities") or []
        out.append(
            PromotedNote(
                note_id=path.stem,
                title=meta.get("title", path.stem),
                date=file_date,
                body=_strip_note_trailer(body),
                entities=tuple(str(e) for e in entities),
                updated_at=_as_str(meta.get("updated_at")),
                session_id=_as_str(meta.get("session_id")),
            )
        )
    return out


# Footer lines NA appends after the trailing `---`: a source-ref line (`Source:`
# or a bare URL) and/or a `**Bold:**` reflection. A trailing block made only of
# these is provenance/reflection metadata, not the note's claim text.
_FOOTER_PREFIXES = ("**", "Source:", "http://", "https://")
# The subset that's an UNAMBIGUOUS footer marker even without a `---` rule above
# it — a `**Bold:**` line alone is left (it could be real content).
_UNDELIMITED_FOOTER = ("Source:", "http://", "https://")


def _strip_note_trailer(body: str) -> str:
    """Drop NA's appended footer so it doesn't render as prose on the wiki page,
    where the whole note body becomes a verbatim derived claim. Two footer shapes:

    1. `---`-delimited: the final `---` separator plus a trailing block made only
       of footer-shaped lines. A `---` with real prose after it (a legitimate
       mid-body horizontal rule) is left intact.
    2. undelimited: a bare trailing `Source:` / URL line with no `---` above it.

    ponytail: a trailing `**Bold:**` with no `---` is left — too ambiguous to cut
    (could be a real closing line like `**Conclusion:** …`)."""
    lines = body.rstrip().split("\n")
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() != "---":
            continue
        trailing = [ln.strip() for ln in lines[i + 1 :] if ln.strip()]
        if trailing and all(ln.startswith(_FOOTER_PREFIXES) for ln in trailing):
            lines = lines[:i]
        break  # the last `---` isn't a footer delimiter — don't hunt earlier ones
    while lines and (not lines[-1].strip() or lines[-1].strip().startswith(_UNDELIMITED_FOOTER)):
        lines.pop()
    return "\n".join(lines).rstrip()


def _as_str(value: object) -> str | None:
    """Frontmatter scalars may parse as non-str (e.g. a datetime); keep the
    ISO text form and treat missing/empty as None."""
    return str(value) if value not in (None, "") else None
