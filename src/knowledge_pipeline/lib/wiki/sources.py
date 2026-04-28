# Source adapters for wiki ingest — pure generators, no state filtering.

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

import yaml

from knowledge_pipeline.lib.store import ContentRow, get_contents


@dataclass
class IngestItem:
    """Normalized input shape for wiki ingest."""

    item_id: str
    title: str
    date: date | None
    text: str
    source_type: str  # "raw_store" | "local_file"
    source_ref: str  # e.g. "raw_store:content_123" or "local:notes.md"


class IngestSource(Protocol):
    """Protocol for wiki ingest sources — pure generators."""

    def get_items(self) -> list[IngestItem]: ...


class RawStoreSource:
    """Yields IngestItems from raw_store.db."""

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path

    def get_items(self) -> list[IngestItem]:
        kwargs = {}
        if self._db_path is not None:
            kwargs["db_path"] = self._db_path
        rows: list[ContentRow] = get_contents(**kwargs)
        return [self._to_item(r) for r in rows]

    @staticmethod
    def _to_item(row: ContentRow) -> IngestItem:
        return IngestItem(
            item_id=row.content_id,
            title=row.title,
            date=row.content_date,
            text=row.content_md,
            source_type="raw_store",
            source_ref=f"raw_store:{row.content_id}",
        )


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

    @staticmethod
    def _to_item(path: Path) -> IngestItem:
        raw = path.read_text(encoding="utf-8")
        text, meta = _strip_frontmatter(raw)

        # item_id = hash of path + content for dedup
        h = hashlib.sha256(f"{path.name}:{raw}".encode()).hexdigest()[:16]

        # title from frontmatter or filename
        title = meta.get("title", path.stem.replace("_", " ").replace("-", " "))

        # date from frontmatter or filename prefix (e.g. 2026-04-21_notes.md)
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
