"""Relevance-filtered candidate catalog for entity extraction.

The extraction prompt embeds the known-entity catalog so the LLM can reuse an
existing surrogate id. Dumping the *whole* catalog as YAML scales poorly — at a
few hundred entities the prompt bloats, cost creeps, and the LLM's attention is
diluted by off-topic distractors. This module trims the catalog to the entities
lexically relevant to the article before it goes into the prompt.

Pure + dependency-free by design (no spacy / NLP model — a regex tokenizer plus
a small stopword set). Lexical, not semantic: matching is word-level set overlap
over each entity's canonical name + aliases, so "MCP" in the article still hits
an entity whose alias is "MCP" even when its canonical is "Model Context
Protocol". It is blind to pure paraphrase, but the resolver's fuzzy gate is the
backstop for that (a missed catalog hit mints a near-dup, caught by curated
merge — never a wrong merge).

Selection is THRESHOLD-GATED: a catalog at or below `max_entities` passes
through untouched (preserves today's behaviour at small scale), so filtering
only engages once the catalog is large enough to be worth trimming. If filtering
would empty the catalog (no lexical overlap at all), the full store is returned
rather than hiding every entity.
"""

import re

from domains.wiki.aliases import AliasStore

# Default cap on entities passed to the extraction prompt; also the gate — a
# catalog this size or smaller is passed through unfiltered.
RELEVANCE_MAX_ENTITIES = 50

# A small, deliberately minimal English stopword set — enough that function
# words don't match every entity, not a full NLP stoplist.
_STOPWORDS = frozenset(
    """
    a an and are as at be but by for from has have he her his i if in into is it
    its me my no not of on or our over she so than that the their them then there
    these they this to us was we were will with you your
    """.split()
)

# Unicode word tokens minus underscore — letters/digits across scripts, so a
# non-ASCII entity (e.g. "Beyoncé", a CJK concept) is tokenised, not dropped.
_TOKEN_RE = re.compile(r"[^\W_]+")


def _tokens(text: str) -> set[str]:
    """Word tokens (length ≥ 2), lowercased, minus stopwords. Empty text → empty
    set, which makes the catalog filter fall back to the full store upstream."""
    return {t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= 2 and t not in _STOPWORDS}


def extract_keywords(text: str) -> set[str]:
    """Keyword set for an article — its content tokens, stopwords removed."""
    return _tokens(text)


def filter_catalog_by_relevance(
    store: AliasStore, keywords: set[str], *, max_entities: int
) -> AliasStore:
    """Keep entities whose canonical/alias tokens overlap the article keywords,
    ranked by overlap count, truncated to `max_entities`. Word-level overlap (not
    substring) so short keywords can't spuriously match ("ai" never hits
    "chair")."""
    scored: list[tuple[int, str, object]] = []
    for entity_id, entry in store.entries.items():
        entity_tokens = _tokens(f"{entry.canonical} {' '.join(entry.aliases)}")
        overlap = len(entity_tokens & keywords)
        if overlap:
            scored.append((overlap, entity_id, entry))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return AliasStore(entries={eid: entry for _, eid, entry in scored[:max_entities]})


def select_relevant_entities(
    store: AliasStore, text: str, *, max_entities: int = RELEVANCE_MAX_ENTITIES
) -> AliasStore:
    """The catalog to send to the extraction prompt for this article.

    Threshold-gated: a catalog at or below `max_entities` is returned unchanged.
    Above it, the catalog is filtered to the keyword-relevant entities (capped at
    `max_entities`); an empty filter result falls back to the full store so no
    entity is ever hidden when nothing lexically matched.
    """
    if len(store.entries) <= max_entities:
        return store
    filtered = filter_catalog_by_relevance(store, extract_keywords(text), max_entities=max_entities)
    return filtered if filtered.entries else store
