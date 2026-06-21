"""WikiSource — read synthesized wiki pages from ``data/wiki/`` as IngestItems.

Mirrors the other source adapters (``raw_store``, ``research``, ``sessions``,
``notes``) so the index/eval pipelines treat wiki pages as just another source.

Each page becomes one ``IngestItem`` whose ``text`` is the page **summary**
(the one-sentence document-shape distillation), not the body — the summary is
the resurfacing unit the recall layer speaks. ``num_sources`` is carried
through for the index-time sparsity gate (W3).

Identity is **frontmatter-authoritative**: ``entity_id`` always comes from the
page's frontmatter, never inferred from the file path. The common path of a
well-formed corpus has ``entity_id == {dir}__{stem}``, so ``get_item`` builds
the path directly; but a dedup-track failure mode lets a page's frontmatter id
disagree with its path (e.g. ``concept__x`` frontmatter at ``trend/x.md``).
``get_item`` therefore verifies the frontmatter id and falls back to a scan on
a miss, so it never returns a page whose id differs from the request and always
resolves whatever ``get_item_ids`` advertises.

Malformed frontmatter (missing ``entity_id`` / ``title`` / ``updated_at``) is a
producer bug — the adapter fails loud rather than silently skipping the page.
"""

from datetime import date
from pathlib import Path

from domains.types import IngestItem
from domains.wiki.io import read_meta


class WikiSource:
    """Yields IngestItems from a ``data/wiki/`` directory of ``.md`` pages."""

    def __init__(self, wiki_dir: Path):
        self._wiki_dir = Path(wiki_dir)

    def get_item_ids(self) -> list[str]:
        return sorted(read_meta(p)["entity_id"] for p in self._page_paths())

    def get_item(self, item_id: str) -> IngestItem | None:
        if "__" not in item_id:
            return None
        page_type, slug = item_id.split("__", 1)
        # Fast path: a well-formed page lives at {page_type}/{slug}.md and its
        # frontmatter id matches. Verify the id so a path collision can't return
        # the wrong page.
        path = self._wiki_dir / page_type / f"{slug}.md"
        if path.is_file():
            meta = read_meta(path)
            if meta.get("entity_id") == item_id:
                return _item_from_meta(meta)
        # Collision fallback: resolve by frontmatter authority so get_item
        # agrees with get_item_ids even when a page's id disagrees with its path.
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

    def _page_paths(self) -> list[Path]:
        """Page ``.md`` files under page-type subdirs, skipping sidecar dirs
        like ``_index/`` (aliases.json / TOC) that carry no page frontmatter."""
        return [p for p in self._wiki_dir.glob("*/*.md") if not p.parent.name.startswith("_")]


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
