"""Pure helpers for turning LLM page output into a WikiPage.

Lifted out of the legacy ingest.py so the new workflow doesn't depend on
the soon-to-be-deleted module. Behavior is identical — same frontmatter
parsing, same enforcement of expected entity_id and page_type, same H2
preservation warning.
"""

import logging
import re
from datetime import date
from pathlib import Path

import yaml
from domains.wiki.types import WikiPage

logger = logging.getLogger(__name__)


def parse_llm_page_output(
    *,
    raw: str,
    entity_id: str,
    title: str,
    page_type: str,
    related: list[str],
    source_id: str,
) -> WikiPage:
    """Parse LLM output into a WikiPage, falling back to defaults for bad frontmatter.

    LLMs sometimes hallucinate a different entity_id or page_type — we always
    overwrite those with what the caller asked for to keep the page index stable.
    """
    raw = raw.strip()

    if raw.startswith("---"):
        rest = raw[3:]
        end = rest.find("\n---")
        if end != -1:
            yaml_str = rest[:end]
            content = rest[end + 4 :].strip()
            try:
                meta = yaml.safe_load(yaml_str)
                if isinstance(meta, dict):
                    llm_entity_id = meta.get("entity_id", entity_id)
                    if llm_entity_id != entity_id:
                        logger.warning(
                            "LLM returned entity_id '%s' but expected '%s', using expected",
                            llm_entity_id,
                            entity_id,
                        )
                    return WikiPage(
                        entity_id=entity_id,
                        title=meta.get("title", title),
                        page_type=page_type,
                        related=meta.get("related", related),
                        sources=meta.get("sources", [source_id]),
                        updated_at=date.today(),
                        content=content,
                    )
            except (yaml.YAMLError, ValueError):
                logger.warning("Bad frontmatter from LLM for %s, using defaults", entity_id)

    return WikiPage(
        entity_id=entity_id,
        title=title,
        page_type=page_type,
        related=related,
        sources=[source_id],
        updated_at=date.today(),
        content=raw,
    )


def check_h2_preservation(page_path: Path, new_content: str) -> None:
    """Warn if existing H2 sections were dropped in the merge."""
    old_text = page_path.read_text(encoding="utf-8")
    old_h2s = set(re.findall(r"^## (.+)$", old_text, re.MULTILINE))
    new_h2s = set(re.findall(r"^## (.+)$", new_content, re.MULTILINE))

    dropped = old_h2s - new_h2s
    if dropped:
        logger.warning(
            "Page %s: H2 sections dropped in merge: %s",
            page_path.name,
            ", ".join(sorted(dropped)),
        )


def slug_from_id(entity_id: str) -> str:
    """Extract slug from entity_id (e.g. 'concept__rag' -> 'rag')."""
    parts = entity_id.split("__", 1)
    return parts[1] if len(parts) == 2 else entity_id
