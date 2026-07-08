"""WikiSource — read synthesized wiki pages from ``data/wiki/`` as IngestItems.

Mirrors the other source adapters (``raw_store``, ``sessions``, ``notes``) so
the index/eval pipelines treat wiki pages as just another source.

Each page becomes one ``IngestItem`` whose ``text`` is the page **summary**
(the one-sentence document-shape distillation), not the body — the summary is
the resurfacing unit the recall layer speaks. ``num_sources`` is carried
through for the index-time sparsity gate (W3).

Layout: pages are flat ``.md`` files directly under ``data/wiki/`` named
``{slug}-{shortid}.md``; ``index.md`` (the TOC) and ``_index/`` sidecars
(aliases.json) carry no page frontmatter and are skipped.

Identity is **frontmatter-authoritative**: ``entity_id`` is the opaque surrogate
(``e_<hex>``) read from the page's frontmatter, never derived from the filename.
``get_item`` therefore resolves by scanning for the page whose frontmatter id
matches the request, so it never returns a page whose id differs from the
request and always resolves whatever ``get_item_ids`` advertises.

Malformed frontmatter (missing ``entity_id`` / ``title`` / ``updated_at``) is a
producer bug — the adapter fails loud rather than silently skipping the page.
"""

import json
from datetime import date
from pathlib import Path

from domains.types import IngestItem
from domains.wiki.io import read_meta

# Root-level ``.md`` files that are not entity pages (no page frontmatter).
_NON_PAGE_FILES = frozenset({"index.md"})


class WikiSource:
    """Yields IngestItems from a ``data/wiki/`` directory of ``.md`` pages."""

    def __init__(self, wiki_dir: Path):
        self._wiki_dir = Path(wiki_dir)

    def get_item_ids(self) -> list[str]:
        return sorted(read_meta(p)["entity_id"] for p in self._page_paths())

    def get_item(self, item_id: str) -> IngestItem | None:
        # The surrogate id is opaque — nothing in the filename is derivable from
        # it — so resolve by frontmatter authority: scan for the matching id.
        for p in self._page_paths():
            meta = read_meta(p)
            if meta.get("entity_id") == item_id:
                return _item_from_meta(meta)
        return None

    def get_items(self) -> list[IngestItem]:
        return sorted(
            (_item_from_meta(read_meta(p)) for p in self._page_paths()),
            key=lambda item: item.item_id,
        )

    def resolve_index(self) -> dict[str, dict]:
        """Per-entity provenance from ``_index/resolve.json``:
        ``{entity_id: {"page_hash": …, "snapshot_id": …, "num_sources": …}}``.

        resolve.json is the single provenance authority for the vector lane —
        all three fields come from the same snapshot so they stay mutually
        consistent. ``page_hash`` is per-entity (changes when a page's bytes
        change); ``snapshot_id`` is the tick-wide fingerprint fanned onto every
        entity; ``num_sources`` is the distinct-source count. The lane stamps
        them into each embedded page's metadata so the recall side can detect a
        stale hit + hedge a single-source page (FM2/FM4) and this side can
        re-embed a changed page (FM1b). Returns ``{}`` when the sidecar isn't
        written yet — index-building and vector-population run independently.
        """
        try:
            resolve = json.loads(
                (self._wiki_dir / "_index" / "resolve.json").read_text(encoding="utf-8")
            )
        except (FileNotFoundError, ValueError):
            return {}
        snapshot_id = resolve.get("snapshot_id")
        return {
            entity_id: {
                "page_hash": e.get("page_hash"),
                "snapshot_id": snapshot_id,
                "num_sources": e.get("num_sources"),
            }
            for entity_id, e in resolve.get("entities", {}).items()
        }

    def _page_paths(self) -> list[Path]:
        """Flat page ``.md`` files directly under the wiki dir, skipping
        ``index.md`` (the TOC). ``glob("*.md")`` is non-recursive, so ``_index/``
        sidecars never appear."""
        return [p for p in self._wiki_dir.glob("*.md") if p.name not in _NON_PAGE_FILES]


def _item_from_meta(meta: dict) -> IngestItem:
    updated = meta["updated_at"]
    if not isinstance(updated, date):
        updated = date.fromisoformat(str(updated))
    return IngestItem(
        item_id=meta["entity_id"],
        title=meta["title"],
        date=updated,
        text=meta.get("summary", ""),
        source_type="wiki",
        source_ref=f"wiki:{meta['entity_id']}",
        num_sources=meta.get("num_sources"),
    )
