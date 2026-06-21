"""WikiSource — read synthesized wiki pages from ``data/wiki/`` as IngestItems.

Mirrors the other source adapters (``raw_store``, ``research``, ``sessions``,
``notes``) so the index/eval pipelines treat wiki pages as just another source.

Each page becomes one ``IngestItem`` whose ``text`` is the page **summary**
(the one-sentence document-shape distillation), not the body — the summary is
the resurfacing unit the recall layer speaks. ``num_sources`` is carried
through for the index-time sparsity gate (W3).
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
        path = self._wiki_dir / page_type / f"{slug}.md"
        return _to_item(path) if path.is_file() else None

    def get_items(self) -> list[IngestItem]:
        return [_to_item(path) for path in sorted(self._page_paths())]

    def _page_paths(self) -> list[Path]:
        """Page ``.md`` files under page-type subdirs, skipping sidecar dirs
        like ``_index/`` (aliases.json / TOC) that carry no page frontmatter."""
        return [p for p in self._wiki_dir.glob("*/*.md") if not p.parent.name.startswith("_")]


def _to_item(path: Path) -> IngestItem:
    meta = read_meta(path)
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
