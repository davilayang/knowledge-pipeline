"""The synthesis prompt templates must LEAD with the shared article block.

Within one item every entity is synthesised against the same article, so a
leading article makes `[system + article]` a constant prefix that OpenAI's
automatic prompt caching reuses across the entity loop. These tests fail if a
future edit moves the per-entity fields back to the top (breaking caching)."""

from workflows.wiki_synthesis.prompts import (
    PAGE_SYNTHESIS_USER_CREATE,
    PAGE_SYNTHESIS_USER_UPDATE,
)

_FIELDS = dict(
    source_id="content_x",
    article_title="An Article",
    article_text="ARTICLE_BODY",
    entity_id="e_abc",
    title="Ent",
    page_type="concept",
    related="e_y",
)


def test_create_prompt_leads_with_article():
    rendered = PAGE_SYNTHESIS_USER_CREATE.format(**_FIELDS)
    assert rendered.index("ARTICLE_BODY") < rendered.index("e_abc")


def test_update_prompt_leads_with_article_then_per_entity_fields():
    rendered = PAGE_SYNTHESIS_USER_UPDATE.format(**_FIELDS, existing_page="OLD_PAGE")
    # article first, then the per-entity id and the per-entity existing page
    assert rendered.index("ARTICLE_BODY") < rendered.index("e_abc")
    assert rendered.index("ARTICLE_BODY") < rendered.index("OLD_PAGE")


def test_extraction_system_prompt_is_domain_agnostic():
    """The extractor must not be hardcoded to AI/ML — it pages diverse domains,
    so the prompt frames extraction on the article's own terms, not an AI/ML
    lens."""
    from workflows.wiki_synthesis.prompts import ENTITY_EXTRACTION_SYSTEM

    assert "ai/ml and data engineering" not in ENTITY_EXTRACTION_SYSTEM.lower()


def test_every_page_type_is_described_in_prompt():
    """Drift guard: PageType (domains/wiki/types.py) is the single source of
    truth for the allowed types; the prompt carries each type's prose
    description. This fails if a type is added/renamed in the Literal without
    describing it in the prompt — so adding a type can't silently leave the LLM
    unable to assign it (structured output would then force the wrong type)."""
    from typing import get_args

    from domains.wiki.types import PageType
    from workflows.wiki_synthesis.prompts import ENTITY_EXTRACTION_SYSTEM

    lowered = ENTITY_EXTRACTION_SYSTEM.lower()
    for page_type in get_args(PageType):
        assert f"{page_type}:" in lowered, f"PageType {page_type!r} not described in the prompt"


def test_prompts_load_from_files_non_empty():
    """The constants resolve from prompts/wiki/*.md (KP_PROMPTS_ROOT) — a missing
    or misnamed file / broken loader fails here, not at synthesis time."""
    from workflows.wiki_synthesis.prompts import (
        ENTITY_EXTRACTION_SYSTEM,
        ENTITY_EXTRACTION_USER,
        PAGE_SYNTHESIS_SYSTEM,
    )

    assert ENTITY_EXTRACTION_SYSTEM.strip()
    assert PAGE_SYNTHESIS_SYSTEM.strip()
    assert "{article_text}" in ENTITY_EXTRACTION_USER
