# Core wiki ingest pipeline — entity extraction + page synthesis.

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

import yaml

from knowledge_pipeline.lib.llm import generate, generate_structured
from knowledge_pipeline.lib.wiki.aliases import AliasStore, load_aliases, save_aliases
from knowledge_pipeline.lib.wiki.io import write_page
from knowledge_pipeline.lib.wiki.prompts import (
    ENTITY_EXTRACTION_SYSTEM,
    ENTITY_EXTRACTION_USER,
    PAGE_SYNTHESIS_SYSTEM,
    PAGE_SYNTHESIS_USER_CREATE,
    PAGE_SYNTHESIS_USER_UPDATE,
)
from knowledge_pipeline.lib.wiki.sources import IngestItem
from knowledge_pipeline.lib.wiki.state import WikiStateDB
from knowledge_pipeline.lib.wiki.types import ExtractionResult, WikiPage

logger = logging.getLogger(__name__)

EXTRACTION_MODEL = "gpt-4.1-nano"
SYNTHESIS_MODEL = "gpt-4.1-mini"


def ingest_article(
    item: IngestItem,
    *,
    wiki_dir: Path,
    aliases_path: Path,
    state_db: WikiStateDB,
) -> list[str]:
    """Process a single article through entity extraction and page synthesis.

    Returns list of entity_ids that were created/updated.
    Raises on unrecoverable error; caller should catch and mark as failed.
    """
    # Load current aliases (fresh each article to pick up prior writes)
    alias_store = load_aliases(aliases_path)
    aliases_yaml = aliases_path.read_text(encoding="utf-8") if aliases_path.exists() else ""

    # --- Call 1: Entity extraction ---
    extraction = _extract_entities(item, aliases_yaml)
    if not extraction.entities:
        logger.info("No entities extracted from %s, skipping", item.item_id)
        return []

    # Stage alias updates in memory (don't persist yet)
    staged_aliases = _stage_alias_updates(alias_store, extraction)

    # --- Call 2: Page synthesis (1 call per page) ---
    pages_touched = []
    for entity in extraction.entities:
        page_path = wiki_dir / entity.page_type / f"{_slug_from_id(entity.entity_id)}.md"
        is_update = page_path.exists()

        try:
            new_page = _synthesize_page(
                item=item,
                entity_id=entity.entity_id,
                title=entity.title,
                page_type=entity.page_type,
                related=[
                    e.entity_id for e in extraction.entities if e.entity_id != entity.entity_id
                ],
                page_path=page_path,
                is_update=is_update,
            )
        except Exception:
            logger.exception(
                "Failed to synthesize page %s for article %s", entity.entity_id, item.item_id
            )
            continue

        # Validate: if update, check no H2 sections were dropped
        if is_update:
            _check_h2_preservation(page_path, new_page.content)

        # Atomic write
        write_page(page_path, new_page)

        # Update state DB catalog
        state_db.upsert_page(
            entity_id=new_page.entity_id,
            page_type=new_page.page_type,
            file_path=str(page_path.relative_to(wiki_dir)),
            updated_at=new_page.updated_at.isoformat(),
            related=new_page.related,
        )

        pages_touched.append(entity.entity_id)

    # Only persist aliases after all page writes succeed
    if pages_touched:
        _apply_staged_aliases(alias_store, staged_aliases)
        save_aliases(aliases_path, alias_store)

    return pages_touched


def _extract_entities(item: IngestItem, aliases_yaml: str) -> ExtractionResult:
    """Call 1: Extract entities from article using structured output."""
    user_prompt = ENTITY_EXTRACTION_USER.format(
        aliases_yaml=aliases_yaml or "(no existing aliases)",
        title=item.title,
        article_text=item.text,
    )
    return generate_structured(
        user_prompt,
        schema=ExtractionResult,
        system=ENTITY_EXTRACTION_SYSTEM,
        model=EXTRACTION_MODEL,
    )


def _synthesize_page(
    *,
    item: IngestItem,
    entity_id: str,
    title: str,
    page_type: str,
    related: list[str],
    page_path: Path,
    is_update: bool,
) -> WikiPage:
    """Call 2: Synthesize or update a single wiki page."""
    if is_update:
        existing_page = page_path.read_text(encoding="utf-8")
        user_prompt = PAGE_SYNTHESIS_USER_UPDATE.format(
            entity_id=entity_id,
            title=title,
            page_type=page_type,
            related=", ".join(related),
            existing_page=existing_page,
            source_id=item.item_id,
            article_title=item.title,
            article_text=item.text,
        )
    else:
        user_prompt = PAGE_SYNTHESIS_USER_CREATE.format(
            entity_id=entity_id,
            title=title,
            page_type=page_type,
            related=", ".join(related),
            source_id=item.item_id,
            article_title=item.title,
            article_text=item.text,
        )

    raw_output = generate(
        user_prompt,
        system=PAGE_SYNTHESIS_SYSTEM,
        model=SYNTHESIS_MODEL,
    )

    return _parse_llm_page_output(raw_output, entity_id, title, page_type, related, item.item_id)


def _parse_llm_page_output(
    raw: str,
    entity_id: str,
    title: str,
    page_type: str,
    related: list[str],
    source_id: str,
) -> WikiPage:
    """Parse LLM output into a WikiPage, falling back to defaults for bad frontmatter."""
    raw = raw.strip()

    # Try to extract frontmatter
    if raw.startswith("---"):
        rest = raw[3:]
        end = rest.find("\n---")
        if end != -1:
            yaml_str = rest[:end]
            content = rest[end + 4 :].strip()
            try:
                meta = yaml.safe_load(yaml_str)
                if isinstance(meta, dict):
                    return WikiPage(
                        entity_id=meta.get("entity_id", entity_id),
                        title=meta.get("title", title),
                        page_type=meta.get("page_type", page_type),
                        related=meta.get("related", related),
                        sources=meta.get("sources", [source_id]),
                        updated_at=date.today(),
                        content=content,
                    )
            except (yaml.YAMLError, ValueError):
                logger.warning("Bad frontmatter from LLM for %s, using defaults", entity_id)

    # Fallback: treat entire output as content
    return WikiPage(
        entity_id=entity_id,
        title=title,
        page_type=page_type,
        related=related,
        sources=[source_id],
        updated_at=date.today(),
        content=raw,
    )


def _check_h2_preservation(page_path: Path, new_content: str) -> None:
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


def _stage_alias_updates(
    store: AliasStore, extraction: ExtractionResult
) -> list[tuple[str, str, list[str]]]:
    """Collect alias updates without persisting. Returns list of (entity_id, canonical, aliases)."""
    staged = []
    for entity in extraction.entities:
        if entity.is_new and entity.entity_id not in store.entries:
            staged.append((entity.entity_id, entity.title, entity.aliases))
    return staged


def _apply_staged_aliases(store: AliasStore, staged: list[tuple[str, str, list[str]]]) -> None:
    """Apply staged alias updates to the store."""
    for entity_id, canonical, aliases in staged:
        store.add(entity_id, canonical, aliases)


def _slug_from_id(entity_id: str) -> str:
    """Extract slug from entity_id (e.g. 'concept__rag' -> 'rag')."""
    parts = entity_id.split("__", 1)
    return parts[1] if len(parts) == 2 else entity_id
