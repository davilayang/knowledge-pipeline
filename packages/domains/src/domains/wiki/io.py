import os
from datetime import date
from pathlib import Path

import yaml

from domains.wiki.types import WikiPage

_FRONTMATTER_DELIMITER = "---"


def read_meta(path: Path) -> dict:
    """Read just the YAML frontmatter of a wiki page as a dict.

    Exposes fields that ``WikiPage`` doesn't carry (e.g. ``num_sources``, which
    is producer-authoritative and written separately from the typed page).
    """
    meta, _ = _split_frontmatter(path.read_text(encoding="utf-8"))
    return meta


def read_page(path: Path) -> WikiPage:
    """Read a wiki page from a markdown file with YAML frontmatter."""
    text = path.read_text(encoding="utf-8")
    meta, content = _split_frontmatter(text)
    return WikiPage(
        entity_id=meta["entity_id"],
        title=meta["title"],
        entity_type=meta["entity_type"],
        summary=meta.get("summary", ""),
        related=meta.get("related", []),
        sources=meta.get("sources", []),
        updated_at=(
            meta["updated_at"]
            if isinstance(meta["updated_at"], date)
            else date.fromisoformat(str(meta["updated_at"]))
        ),
        content=content,
    )


def write_page(
    path: Path,
    page: WikiPage,
    *,
    aliases: list[str],
    num_sources: int,
    sources: list[str],
    related: list[str],
) -> None:
    """Write a wiki page to a markdown file with YAML frontmatter.

    `aliases`, `num_sources`, `sources`, and `related` are producer-authoritative
    (sourced from the wiki.db state at write time), not LLM-supplied.
    `sources` is the accumulated distinct source ids for the entity (NOT
    page.sources, the single triggering [source_id]); `related` is the
    accumulated co-occurrence neighbours derived from claim_entities (NOT
    page.related, this article's siblings). The frontmatter key order is stable
    for diff-readability:

        entity_id, title, entity_type, summary, aliases, related, sources,
        num_sources, updated_at

    Uses atomic write: writes to a .tmp file first, then os.replace.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")

    frontmatter_lines = [
        f"entity_id: {_yaml_scalar(page.entity_id)}",
        f"title: {_yaml_scalar(page.title)}",
        f"entity_type: {_yaml_scalar(page.entity_type)}",
        f"summary: {_yaml_scalar(page.summary)}",
        f"aliases: {_yaml_inline_list(aliases)}",
        f"related: {_yaml_inline_list(related)}",
        f"sources: {_yaml_inline_list(sources)}",
        f"num_sources: {int(num_sources)}",
        f"updated_at: {page.updated_at.isoformat()}",
    ]

    lines = [
        _FRONTMATTER_DELIMITER,
        "\n".join(frontmatter_lines),
        _FRONTMATTER_DELIMITER,
        "",
        page.content,
    ]
    output = "\n".join(lines)
    if not output.endswith("\n"):
        output += "\n"

    tmp_path.write_text(output, encoding="utf-8")
    os.replace(tmp_path, path)


def _yaml_scalar(value: str) -> str:
    """Format a scalar string for inline YAML emission.

    Round-trips through yaml.dump for a single-key mapping so we get correct
    quoting/escaping (including the empty-string case) without re-implementing
    YAML's escape rules.
    """
    dumped = yaml.dump({"_": value}, default_flow_style=False, sort_keys=False).rstrip()
    return dumped[len("_: ") :]


def _yaml_inline_list(items: list[str]) -> str:
    """Format a list of strings in inline `[a, b]` form for frontmatter."""
    return yaml.dump(items, default_flow_style=True, sort_keys=False).rstrip()


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split markdown text into frontmatter dict and body content."""
    text = text.strip()
    if not text.startswith(_FRONTMATTER_DELIMITER):
        raise ValueError("File does not start with frontmatter delimiter '---'")

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
