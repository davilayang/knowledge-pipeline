# Read/write wiki pages as markdown files with YAML frontmatter.

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import yaml

from knowledge_pipeline.lib.wiki.types import WikiPage

_FRONTMATTER_DELIMITER = "---"


def read_page(path: Path) -> WikiPage:
    """Read a wiki page from a markdown file with YAML frontmatter."""
    text = path.read_text(encoding="utf-8")
    meta, content = _split_frontmatter(text)
    return WikiPage(
        entity_id=meta["entity_id"],
        title=meta["title"],
        page_type=meta["page_type"],
        related=meta.get("related", []),
        sources=meta.get("sources", []),
        updated_at=(
            meta["updated_at"]
            if isinstance(meta["updated_at"], date)
            else date.fromisoformat(str(meta["updated_at"]))
        ),
        content=content,
    )


def write_page(path: Path, page: WikiPage) -> None:
    """Write a wiki page to a markdown file with YAML frontmatter.

    Uses atomic write: writes to a .tmp file first, then os.replace.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")

    frontmatter = {
        "entity_id": page.entity_id,
        "title": page.title,
        "page_type": page.page_type,
        "related": page.related,
        "sources": page.sources,
        "updated_at": page.updated_at.isoformat(),
    }

    lines = [
        _FRONTMATTER_DELIMITER,
        yaml.dump(frontmatter, default_flow_style=False, sort_keys=False).rstrip(),
        _FRONTMATTER_DELIMITER,
        "",
        page.content,
    ]
    output = "\n".join(lines)
    if not output.endswith("\n"):
        output += "\n"

    tmp_path.write_text(output, encoding="utf-8")
    os.replace(tmp_path, path)


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split markdown text into frontmatter dict and body content."""
    text = text.strip()
    if not text.startswith(_FRONTMATTER_DELIMITER):
        raise ValueError("File does not start with frontmatter delimiter '---'")

    # Find second delimiter
    rest = text[len(_FRONTMATTER_DELIMITER) :]
    end_idx = rest.find(f"\n{_FRONTMATTER_DELIMITER}")
    if end_idx == -1:
        raise ValueError("Could not find closing frontmatter delimiter '---'")

    yaml_str = rest[:end_idx]
    body = rest[end_idx + len(f"\n{_FRONTMATTER_DELIMITER}") :].lstrip("\n")

    meta = yaml.safe_load(yaml_str)
    if not isinstance(meta, dict):
        raise ValueError("Frontmatter is not a valid YAML mapping")

    return meta, body
