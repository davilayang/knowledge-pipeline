"""Relevance-filtered candidate catalog (domains.wiki.relevance).

Pure functions: keyword extraction (no spacy — a regex tokenizer + a small
stopword set) and lexical filtering of the known-entity catalog before it goes
into the extraction prompt. The selection wrapper is threshold-gated so small
catalogs pass through unchanged (today's behaviour) and only large catalogs are
trimmed to the relevant entities.
"""

from domains.wiki.aliases import AliasEntry, AliasStore
from domains.wiki.relevance import (
    extract_keywords,
    filter_catalog_by_relevance,
    select_relevant_entities,
)


def test_extract_keywords_tokenizes_and_drops_stopwords():
    kws = extract_keywords("The MCP protocol connects an AI agent to the database.")
    assert "mcp" in kws
    assert "protocol" in kws
    assert "agent" in kws
    # Common stopwords are removed so they can't match every entity.
    assert "the" not in kws
    assert "an" not in kws
    assert "to" not in kws


def test_extract_keywords_tokenizes_unicode():
    """Non-ASCII letters are tokenised, not silently dropped, so an accented or
    CJK entity name can still be matched by relevance."""
    kws = extract_keywords("Beyoncé released a café playlist; 知識 graph notes.")
    assert "beyoncé" in kws
    assert "café" in kws
    assert "知識" in kws


def _store(*entities: tuple[str, str, list[str]]) -> AliasStore:
    return AliasStore(
        entries={
            eid: AliasEntry(canonical=name, aliases=aliases) for eid, name, aliases in entities
        }
    )


def test_filter_keeps_keyword_overlap_via_canonical_or_alias():
    store = _store(
        ("e_1", "Model Context Protocol", ["MCP"]),
        ("e_2", "Sourdough Baking", []),
    )
    # Article says "MCP" — matches e_2's... no: matches e_1 via its alias token.
    filtered = filter_catalog_by_relevance(store, {"mcp", "agent"}, max_entities=10)
    assert set(filtered.entries) == {"e_1"}


def test_filter_word_level_not_substring():
    """A short keyword must not substring-match an unrelated entity ("ai" in
    "chair") — overlap is on whole tokens."""
    store = _store(("e_1", "Herman Miller Chair", []))
    assert filter_catalog_by_relevance(store, {"ai"}, max_entities=10).entries == {}


def test_filter_ranks_by_overlap_and_truncates():
    store = _store(
        ("e_1", "retrieval augmented generation", []),  # 2 overlaps
        ("e_2", "retrieval index", []),  # 1 overlap
        ("e_3", "vector database", []),  # 0
    )
    filtered = filter_catalog_by_relevance(store, {"retrieval", "generation"}, max_entities=1)
    assert list(filtered.entries) == ["e_1"]  # highest overlap wins the single slot


def test_select_passes_small_catalog_through_unchanged():
    """At or below the cap, the catalog is returned as-is (no filtering) — today's
    behaviour for a small wiki is preserved."""
    store = _store(("e_1", "Anything", []), ("e_2", "Unrelated", []))
    out = select_relevant_entities(store, "an article about nothing in particular", max_entities=5)
    assert out is store


def test_select_filters_large_catalog_to_relevant():
    store = _store(*[(f"e_{i}", f"Filler Topic {i}", []) for i in range(6)])
    store.entries["e_hit"] = AliasEntry(canonical="Knowledge Graph", aliases=[])
    out = select_relevant_entities(store, "A piece on the knowledge graph.", max_entities=5)
    assert "e_hit" in out.entries
    assert len(out.entries) < len(store.entries)


def test_select_falls_back_to_full_store_when_nothing_matches():
    store = _store(*[(f"e_{i}", f"Filler {i}", []) for i in range(6)])
    out = select_relevant_entities(store, "zzzz qqqq", max_entities=5)
    assert out is store  # empty filter → full store, never hide everything


def test_select_empty_text_on_large_catalog_falls_back_to_full_store():
    """Degenerate input — an item whose text tokenises to nothing — yields no
    keywords, so the filter is empty and we deliberately fall back to the full
    store rather than send an empty catalog. Rare; the alternative (hide every
    entity) would lose the LLM's matched_id linking entirely."""
    store = _store(*[(f"e_{i}", f"Filler {i}", []) for i in range(6)])
    assert select_relevant_entities(store, "", max_entities=5) is store
    assert select_relevant_entities(store, "   \n\t  ", max_entities=5) is store
