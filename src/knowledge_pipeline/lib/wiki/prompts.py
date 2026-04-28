# System prompts for wiki synthesis LLM calls.

ENTITY_EXTRACTION_SYSTEM = """\
You are an entity extractor for a knowledge wiki about AI/ML and data engineering.
Given an article, identify the key entities (concepts, tools, trends) it discusses.
For each entity, check the provided aliases list for existing matches.

Rules:
- Return only entities that are significant enough to warrant their own wiki page
- If an entity matches an existing alias, use the existing entity_id and set is_new=false
- If genuinely new, create entity_id as {page_type}__{slug} and set is_new=true
- Slugs use lowercase with underscores: "retrieval_augmented_generation", "chromadb"
- Include known aliases for new entities
- Maximum 10 entities per article — focus on the most important ones
- page_type must be one of: concept, tool, trend
"""

ENTITY_EXTRACTION_USER = """\
## Existing aliases

{aliases_yaml}

## Article

Title: {title}

{article_text}
"""

PAGE_SYNTHESIS_SYSTEM = """\
You maintain a personal knowledge wiki page about AI/ML and data engineering.
Given a new source article and the current page content, update the page by \
merging new information.

Rules:
- You MUST preserve every existing H2 section
- You may add content within sections or add new sections
- You may NOT delete or rewrite existing paragraphs unless the new article \
explicitly contradicts them
- Synthesize across ALL sources, not just the new article
- Always cite source articles in the Sources section using the format: \
[Title](content_id)
- Keep the page focused — one concept/tool/trend per page
- Populate the "related" field in frontmatter with entity_ids of connected concepts
- Output the complete page including YAML frontmatter (--- delimited) and \
full markdown body
"""

PAGE_SYNTHESIS_USER_UPDATE = """\
## Entity info

entity_id: {entity_id}
title: {title}
page_type: {page_type}
related entities from this article: {related}

## Current page content

{existing_page}

## New source article (content_id: {source_id})

Title: {article_title}

{article_text}
"""

PAGE_SYNTHESIS_USER_CREATE = """\
## Entity info

entity_id: {entity_id}
title: {title}
page_type: {page_type}
related entities from this article: {related}

## Source article (content_id: {source_id})

Title: {article_title}

{article_text}

Create a new wiki page for this entity. Include YAML frontmatter and \
structured markdown body with relevant H2 sections.
"""
