"""LocalFileSource — yields IngestItems from a directory of markdown files."""

import hashlib
from datetime import date
from pathlib import Path

import yaml

from domains.types import IngestItem


class LocalFileSource:
    """Yields IngestItems from local markdown files in a directory."""

    def __init__(self, inbox_dir: Path):
        self._inbox_dir = inbox_dir

    def get_items(self) -> list[IngestItem]:
        if not self._inbox_dir.exists():
            return []

        items = []
        for path in sorted(self._inbox_dir.glob("*.md")):
            items.append(self._to_item(path))
        return items

    def get_item_ids(self) -> list[str]:
        # IDs derive from sha256(filename:content), so enumerating them still
        # requires reading each file. Cheaper than building IngestItems but not
        # cheap.
        if not self._inbox_dir.exists():
            return []
        return [self._to_item(p).item_id for p in sorted(self._inbox_dir.glob("*.md"))]

    def get_item(self, item_id: str) -> IngestItem | None:
        if not self._inbox_dir.exists():
            return None
        for path in sorted(self._inbox_dir.glob("*.md")):
            item = self._to_item(path)
            if item.item_id == item_id:
                return item
        return None

    @staticmethod
    def _to_item(path: Path) -> IngestItem:
        raw = path.read_text(encoding="utf-8")
        text, meta = _strip_frontmatter(raw)

        h = hashlib.sha256(f"{path.name}:{raw}".encode()).hexdigest()[:16]
        title = meta.get("title", path.stem.replace("_", " ").replace("-", " "))

        file_date = meta.get("date")
        if file_date is None:
            file_date = _parse_date_prefix(path.stem)
        elif isinstance(file_date, str):
            file_date = date.fromisoformat(file_date)

        return IngestItem(
            item_id=h,
            title=title,
            date=file_date,
            text=text,
            source_type="local_file",
            source_ref=f"local:{path.name}",
        )


def _strip_frontmatter(text: str) -> tuple[str, dict]:
    """Strip optional YAML frontmatter from text. Returns (body, metadata)."""
    text = text.strip()
    if not text.startswith("---"):
        return text, {}

    rest = text[3:]
    end = rest.find("\n---")
    if end == -1:
        return text, {}

    yaml_str = rest[:end]
    body = rest[end + 4 :].strip()
    meta = yaml.safe_load(yaml_str)
    return body, meta if isinstance(meta, dict) else {}


def _parse_date_prefix(stem: str) -> date | None:
    """Try to parse YYYY-MM-DD from the start of a filename stem."""
    if len(stem) >= 10 and stem[4] == "-" and stem[7] == "-":
        try:
            return date.fromisoformat(stem[:10])
        except ValueError:
            pass
    return None
