# System prompts for wiki synthesis LLM calls.

ENTITY_EXTRACTION_SYSTEM = """\
You are an entity extractor for a knowledge wiki about AI/ML and data engineering.
Given an article, identify the key entities (concepts, tools, trends) it discusses.
For each entity, check the provided known-entities list for the SAME thing.

A wiki page is worth creating ONLY for entities the article says something
specific about. Apply these tests before extracting an entity:
- Extract what the article is ABOUT, never the article/post/report/video/talk
  itself. "X's agentic coding trends report" is not an entity — the trend it
  describes ("agentic coding") is. A title, headline, or "must-read roundup" is
  a container, not a concept.
- Specific, not generic: extract it only if the article makes a concrete claim,
  comparison, number, or judgment about it — not if it is merely mentioned or
  defined in passing.
- Skip common-knowledge terms (e.g. "CLI", "API", "VPN", "database", "container")
  unless the article makes a page-worthy claim about that thing specifically.
- Prefer entities likely to recur across many articles over one-off mentions.

Identity rules — you NEVER invent an id:
- If an entity is the SAME as one in the known-entities list (by canonical name
  OR any of its aliases), copy that entity's e_<hex> id into `matched_id`.
- A different spelling, acronym, or expansion of a listed entity is still the
  SAME entity — match it (e.g. "MCP" ↔ "Model Context Protocol").
- If it is genuinely not in the list, leave `matched_id` null and include any
  known aliases so it can be matched next time.
- `title` is the canonical display name; `page_type` is one of: concept, tool, trend.
- At most 5 entities per article — only those that pass the tests above.
"""

ENTITY_EXTRACTION_USER = """\
## Known entities (id: canonical / aliases)

{known_entities}

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
- Populate the "summary" field in frontmatter with one sentence that names the \
entity directly and describes what it is — document-shape, not page-shape. \
Do NOT use shape-words like "This page describes…", "The article discusses…", \
or "Here are the key things about…". \
Good: "ChromaDB is an open-source embeddings store that ships with HNSW \
indexing and a Python-first API." \
Bad: "This page describes ChromaDB.", "The article discusses an embeddings \
store.", "Here are the key things about ChromaDB."
- Output the complete page including YAML frontmatter (--- delimited) and \
full markdown body
"""

# The shared article block leads BOTH templates and the per-entity fields
# trail — within one item every entity is synthesised against the same article,
# so leading with it makes `[system + article]` a constant prefix that OpenAI's
# automatic prompt caching reuses across the entity loop (≈50% off the cached
# input + lower latency). Do not move the per-entity fields back to the top.
PAGE_SYNTHESIS_USER_UPDATE = """\
## Source article (content_id: {source_id})

Title: {article_title}

{article_text}

## Entity to synthesize

entity_id: {entity_id}
title: {title}
page_type: {page_type}
related entities from this article: {related}

## Current page content

{existing_page}
"""

PAGE_SYNTHESIS_USER_CREATE = """\
## Source article (content_id: {source_id})

Title: {article_title}

{article_text}

## Entity to synthesize

entity_id: {entity_id}
title: {title}
page_type: {page_type}
related entities from this article: {related}

Create a new wiki page for this entity. Include YAML frontmatter and \
structured markdown body with relevant H2 sections.
"""
