"""Tests for domains.wiki.identity — surrogate minting + the resolve_or_mint
batch resolver (pure functions, no DB)."""

import re

from domains.wiki.identity import (
    Candidate,
    EntityIndex,
    EntityRecord,
    mint_surrogate,
    normalize_name,
    resolve_or_mint_batch,
    shortid,
    slugify,
)

NOW = "2026-06-22T00:00:00+00:00"


def _entity(entity_id: str, canonical: str, *, page_type: str = "concept") -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id,
        canonical_name=canonical,
        normalized_name=normalize_name(canonical),
        slug=slugify(canonical),
        page_type=page_type,
        created_at=NOW,
    )


# --- helpers ---


def test_normalize_name_lowers_trims_collapses_ws():
    assert normalize_name("  Model   Costs ") == "model costs"
    assert normalize_name("RAG") == "rag"


def test_mint_surrogate_is_e_plus_16_hex():
    sid = mint_surrogate()
    assert re.fullmatch(r"e_[0-9a-f]{16}", sid)
    assert mint_surrogate() != mint_surrogate()


def test_shortid_is_first_8_hex():
    assert shortid("e_0123456789abcdef") == "01234567"


def test_slugify():
    assert slugify("Model Context Protocol") == "model_context_protocol"
    assert slugify("C++ / Rust!") == "c_rust"


# --- resolve_or_mint_batch ---


def test_exact_normalized_name_reuses_existing_entity():
    index = EntityIndex.build([_entity("e_aaa", "Model Costs")], [])
    res = resolve_or_mint_batch(index, [Candidate(name="model costs", page_type="trend")], now=NOW)

    assert res.resolved[0].entity_id == "e_aaa"
    assert res.resolved[0].is_new is False
    assert res.new_entities == []


def test_miss_mints_new_surrogate():
    index = EntityIndex.build([], [])
    res = resolve_or_mint_batch(
        index, [Candidate(name="Retrieval Augmented Generation", page_type="concept")], now=NOW
    )

    r = res.resolved[0]
    assert r.is_new is True
    assert re.fullmatch(r"e_[0-9a-f]{16}", r.entity_id)
    assert len(res.new_entities) == 1
    e = res.new_entities[0]
    assert e.entity_id == r.entity_id
    assert e.canonical_name == "Retrieval Augmented Generation"
    assert e.normalized_name == "retrieval augmented generation"
    assert e.slug == "retrieval_augmented_generation"
    assert e.page_type == "concept"


def test_within_batch_dedup_collapses_same_normalized_name():
    """Two candidates that normalize to the same name → one entity, one mint."""
    index = EntityIndex.build([], [])
    res = resolve_or_mint_batch(
        index,
        [
            Candidate(name="Model Costs", page_type="concept"),
            Candidate(name="model costs", page_type="trend"),
        ],
        now=NOW,
    )

    assert res.resolved[0].entity_id == res.resolved[1].entity_id
    assert res.resolved[0].is_new is True
    assert res.resolved[1].is_new is False  # second is a reuse of the first mint
    assert len(res.new_entities) == 1
    # First sighting wins canonical + page_type.
    assert res.new_entities[0].canonical_name == "Model Costs"
    assert res.new_entities[0].page_type == "concept"


def test_exact_alias_match_reuses_entity():
    index = EntityIndex.build(
        [_entity("e_mcp", "Model Context Protocol")],
        [("MCP", "e_mcp")],
    )
    res = resolve_or_mint_batch(index, [Candidate(name="mcp", page_type="concept")], now=NOW)

    assert res.resolved[0].entity_id == "e_mcp"
    assert res.resolved[0].is_new is False


def test_matched_id_reuses_when_it_exists_in_the_snapshot():
    """LLM semantic match: name doesn't string-match, but matched_id points at a
    known entity → reuse it (and stage the surface form as an alias)."""
    index = EntityIndex.build([_entity("e_mcp", "Model Context Protocol")], [])
    res = resolve_or_mint_batch(
        index,
        [Candidate(name="the MCP standard", page_type="concept", matched_id="e_mcp")],
        now=NOW,
    )

    assert res.resolved[0].entity_id == "e_mcp"
    assert res.resolved[0].is_new is False
    assert res.new_entities == []


def test_matched_id_ignored_when_not_in_snapshot_then_mints():
    """A hallucinated matched_id (not an existing entity) is ignored; mint instead."""
    index = EntityIndex.build([], [])
    res = resolve_or_mint_batch(
        index,
        [Candidate(name="Brand New Thing", page_type="tool", matched_id="e_ghost")],
        now=NOW,
    )

    assert res.resolved[0].is_new is True
    assert res.resolved[0].entity_id != "e_ghost"


def test_exact_name_beats_matched_id():
    """An exact normalized_name match is AUTHORITATIVE — it wins over the LLM's
    matched_id, which is a weaker (possibly wrong) signal."""
    index = EntityIndex.build(
        [_entity("e_pg", "PostgreSQL"), _entity("e_other", "Some Other Thing")],
        [],
    )
    res = resolve_or_mint_batch(
        index,
        [Candidate(name="PostgreSQL", page_type="tool", matched_id="e_other")],
        now=NOW,
    )

    assert res.resolved[0].entity_id == "e_pg"
    assert res.resolved[0].is_new is False


def test_within_batch_freshly_minted_alias_is_matched():
    """Mint 'Model Context Protocol' with alias 'MCP', then a later 'MCP'
    candidate in the SAME batch must resolve to it, not mint a second entity."""
    index = EntityIndex.build([], [])
    res = resolve_or_mint_batch(
        index,
        [
            Candidate(name="Model Context Protocol", page_type="concept", aliases=["MCP"]),
            Candidate(name="MCP", page_type="concept"),
        ],
        now=NOW,
    )

    assert res.resolved[0].is_new is True
    assert res.resolved[1].is_new is False
    assert res.resolved[0].entity_id == res.resolved[1].entity_id
    assert len(res.new_entities) == 1


def test_resolved_entity_carries_alias_display_forms():
    """A minted entity's ResolvedEntity carries its alias display forms so the
    caller can register them only for surviving candidates."""
    index = EntityIndex.build([], [])
    res = resolve_or_mint_batch(
        index,
        [Candidate(name="ChromaDB", page_type="tool", aliases=["chroma", "chroma-db"])],
        now=NOW,
    )

    assert res.resolved[0].aliases == ("chroma", "chroma-db")


def test_alias_shadowing_existing_canonical_is_dropped():
    """An extracted alias equal to a DIFFERENT entity's canonical name must NOT
    be staged — persisting it would collide with that entity in aliases_index
    (alias 'Long-running agents' vs the canonical of e_lra). Reproduces the
    collision seen in a real rebuild."""
    index = EntityIndex.build([_entity("e_lra", "Long-running agents")], [])
    res = resolve_or_mint_batch(
        index,
        [Candidate(name="Agentic coding", page_type="trend", aliases=["Long-running agents"])],
        now=NOW,
    )

    r = res.resolved[0]
    assert r.is_new is True  # Agentic coding is genuinely new
    assert "Long-running agents" not in r.aliases  # the shadowing alias is dropped
    assert r.aliases == ()


def test_fuzzy_is_advisory_only_and_still_mints():
    """A near-miss must NOT auto-merge into a durable id — it mints a fresh
    entity and records a fuzzy hint for the curated merge."""
    index = EntityIndex.build([_entity("e_rag", "Retrieval Augmented Generation")], [])
    res = resolve_or_mint_batch(
        index,
        [Candidate(name="Retrieval-Augmented Generation", page_type="concept")],
        now=NOW,
    )

    # Minted a NEW entity (false-split is safe; merge is deferred to the curator).
    assert res.resolved[0].is_new is True
    assert res.resolved[0].entity_id != "e_rag"
    # But the near-match was recorded as an advisory hint.
    assert any(hint_id == "e_rag" for _, hint_id in res.fuzzy_hints)
