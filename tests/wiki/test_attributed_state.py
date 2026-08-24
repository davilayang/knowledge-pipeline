"""Tests for domains.wiki.attributed — the claim-centric wiki.db tables
(sources / claims / claim_entities) and the attributed-page render query.

Uses the `wiki_db` fixture (a fresh SQLite wiki.db with the schema applied).
"""

import sqlite3

import pytest
from domains.wiki.attributed import (
    AttributedClaim,
    ClaimRecord,
    SourceRecord,
    attributed_claims_for_entity,
    claim_text_hash,
    count_sources_for_entity,
    delete_claims_for_source,
    get_claims_for_source,
    get_source,
    get_synthesized_watermarks,
    insert_claim,
    insert_claim_entity,
    mint_claim_id,
    mint_source_id,
    render_attributed_markdown,
    upsert_source,
)
from domains.wiki.identity import EntityRecord, normalize_name, slugify
from domains.wiki.state import get_related_for_entity, insert_entity

NOW = "2026-07-02T00:00:00+00:00"


def _entity_record(entity_id, canonical, *, entity_type="concept") -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id,
        canonical_name=canonical,
        normalized_name=normalize_name(canonical),
        slug=slugify(canonical),
        entity_type=entity_type,
        created_at=NOW,
    )


def _seed_entity(conn, entity_id, canonical, *, entity_type="concept") -> EntityRecord:
    ent = _entity_record(entity_id, canonical, entity_type=entity_type)
    insert_entity(conn, ent)
    return ent


def _source(**over) -> SourceRecord:
    base = dict(
        source_id="src_0000000000000001",
        content_key="medium::https://example.com/a",
        origin_type="queue",
        title="A Title",
        author="Jane Doe",
        publication="Codrift",
        url="https://example.com/a",
        published_at="2026-03-01",
        content_hash="hash1",
        fetched_at=NOW,
        added_at=NOW,
    )
    base.update(over)
    return SourceRecord(**base)


# --- sources ---


def test_upsert_source_then_get_roundtrips(wiki_db):
    src = _source()
    upsert_source(wiki_db, src)
    wiki_db.commit()
    assert get_source(wiki_db, "src_0000000000000001") == src


def test_upsert_source_same_content_key_updates_in_place(wiki_db):
    # A re-fetch of the same article (same content_key, freshly-minted source_id)
    # updates the existing row's metadata and keeps the ORIGINAL source_id — so
    # claims already FK'd to it aren't orphaned. Returns the surviving id.
    upsert_source(wiki_db, _source(source_id="src_0000000000000001", title="Old"))
    kept = upsert_source(
        wiki_db,
        _source(source_id="src_9999999999999999", title="New", author="Updated Author"),
    )
    wiki_db.commit()

    assert kept == "src_0000000000000001"
    assert get_source(wiki_db, "src_9999999999999999") is None
    survivor = get_source(wiki_db, "src_0000000000000001")
    assert survivor.title == "New"
    assert survivor.author == "Updated Author"


def test_upsert_source_null_field_keeps_prior_value(wiki_db):
    # A degraded re-fetch (same content_key, some metadata now NULL) must NOT
    # clobber last-known-good attribution — COALESCE keeps the prior value.
    upsert_source(wiki_db, _source(title="Good Title", author="Real Author"))
    upsert_source(wiki_db, _source(source_id="src_degraded", title=None, author=None))
    wiki_db.commit()

    survivor = get_source(wiki_db, "src_0000000000000001")
    assert survivor.title == "Good Title"
    assert survivor.author == "Real Author"


# --- synthesized_at watermark (incremental sweep) ---


def test_synthesized_watermark_roundtrips(wiki_db):
    # The sweep records the max(extracted_at) it consumed as the source's
    # synthesized_at watermark; the next sweep reads it back (keyed by content_key)
    # to skip sources whose extraction docs haven't advanced.
    upsert_source(wiki_db, _source(), synthesized_at="2026-07-02T10:00:00+00:00")
    wiki_db.commit()
    assert get_synthesized_watermarks(wiki_db) == {
        "medium::https://example.com/a": "2026-07-02T10:00:00+00:00"
    }


def test_synthesized_watermark_advances_on_conflict(wiki_db):
    # A re-processed source (same content_key, newer extraction docs) advances the
    # watermark to the newer value.
    upsert_source(wiki_db, _source(), synthesized_at="2026-07-02T10:00:00+00:00")
    upsert_source(
        wiki_db,
        _source(source_id="src_reprocess"),
        synthesized_at="2026-07-03T12:00:00+00:00",
    )
    wiki_db.commit()
    assert get_synthesized_watermarks(wiki_db) == {
        "medium::https://example.com/a": "2026-07-03T12:00:00+00:00"
    }


def test_synthesized_watermark_none_keeps_prior(wiki_db):
    # A re-upsert that doesn't carry a watermark (e.g. the one-off backfill) must
    # not wipe a watermark a prior sweep set — COALESCE keeps last-known-good.
    upsert_source(wiki_db, _source(), synthesized_at="2026-07-02T10:00:00+00:00")
    upsert_source(wiki_db, _source(source_id="src_backfill"))
    wiki_db.commit()
    assert get_synthesized_watermarks(wiki_db) == {
        "medium::https://example.com/a": "2026-07-02T10:00:00+00:00"
    }


def test_synthesized_watermark_absent_source_excluded(wiki_db):
    # A source never given a watermark (NULL synthesized_at) is absent from the
    # map, not present with a None value — the sweep treats "no watermark" as
    # "never synthesized" and processes it.
    upsert_source(wiki_db, _source())
    wiki_db.commit()
    assert get_synthesized_watermarks(wiki_db) == {}


# --- claims ---


def _claim(**over) -> ClaimRecord:
    text = over.pop("text", "Claude Code shipped subagents.")
    base = dict(
        claim_id="clm_0000000000000001",
        source_id="src_0000000000000001",
        text=text,
        text_hash=claim_text_hash(text),
        provenance="source",
        stance="reported",
        created_at=NOW,
    )
    base.update(over)
    return ClaimRecord(**base)


def test_insert_claim_then_get_for_source_roundtrips(wiki_db):
    upsert_source(wiki_db, _source())
    clm = _claim()
    insert_claim(wiki_db, clm)
    wiki_db.commit()
    assert get_claims_for_source(wiki_db, "src_0000000000000001") == [clm]


def test_insert_claim_idempotent_on_source_and_text_hash(wiki_db):
    # Re-extracting a source re-inserts the same claim text (same text_hash) under
    # a new claim_id; the (source_id, text_hash) key collapses it to one row and
    # the first writer's id survives — so a re-run doesn't duplicate claims.
    upsert_source(wiki_db, _source())
    insert_claim(wiki_db, _claim(claim_id="clm_first"))
    insert_claim(wiki_db, _claim(claim_id="clm_second"))
    wiki_db.commit()
    claims = get_claims_for_source(wiki_db, "src_0000000000000001")
    assert [c.claim_id for c in claims] == ["clm_first"]


def test_insert_pipeline_derived_claim_roundtrips(wiki_db):
    # `derived` is reserved for claims the PIPELINE produced by merging or
    # refining other claims — distinct from `user` (a promoted note). Nothing
    # emits these yet; the round-trip locks the third provenance value so the
    # first producer has a place to write to.
    upsert_source(wiki_db, _source())
    clm = _claim(provenance="derived", stance=None)
    insert_claim(wiki_db, clm)
    wiki_db.commit()
    assert get_claims_for_source(wiki_db, "src_0000000000000001") == [clm]


def test_insert_claim_rejects_unknown_provenance(wiki_db):
    # The CHECK is the sole guard (insert_claim does no Python-side validation),
    # so a provenance outside the enum must be rejected — this locks the
    # constraint so a future edit that drops it can't silently pass.
    upsert_source(wiki_db, _source())
    with pytest.raises(sqlite3.IntegrityError):
        insert_claim(wiki_db, _claim(provenance="bogus", stance=None))


def test_insert_claim_rejects_unknown_stance(wiki_db):
    # Same guard on the second axis. Without it, a typo'd stance would persist
    # and quietly drop the claim out of the Reported/Opinion render sections.
    upsert_source(wiki_db, _source())
    with pytest.raises(sqlite3.IntegrityError):
        insert_claim(wiki_db, _claim(provenance="source", stance="bogus"))


def test_delete_claims_for_source_removes_all_and_cascades(wiki_db):
    # A re-extracted source's claims are REPLACED, not merged: delete the source's
    # existing claims (append-only UNIQUE(source_id, text_hash) would otherwise
    # keep stale ones) before re-inserting. ON DELETE CASCADE prunes claim_entities.
    _seed_entity(wiki_db, "e_x", "GraphRAG")
    upsert_source(wiki_db, _source())
    insert_claim(wiki_db, _claim(claim_id="clm_stale", text="stale claim"))
    insert_claim_entity(wiki_db, claim_id="clm_stale", entity_id="e_x")
    wiki_db.commit()

    delete_claims_for_source(wiki_db, "src_0000000000000001")
    wiki_db.commit()

    assert get_claims_for_source(wiki_db, "src_0000000000000001") == []
    assert attributed_claims_for_entity(wiki_db, "e_x") == []


def test_insert_claim_returns_surviving_claim_id(wiki_db):
    # insert_claim returns the id of the row that actually exists (the first
    # writer's, on conflict) — like upsert_source. The persist path links
    # claim_entities to THIS return, so a pre-existing row can't leave the link
    # pointing at a non-existent id (FK break).
    upsert_source(wiki_db, _source())
    assert insert_claim(wiki_db, _claim(claim_id="clm_first")) == "clm_first"
    assert insert_claim(wiki_db, _claim(claim_id="clm_second")) == "clm_first"


# --- claim_entities + attributed-page render ---


def test_attributed_claims_for_entity_dated_first_undated_last(wiki_db):
    # A page renders from the claims attributed to its entity, each carrying its
    # source's attribution (publication / published_at / url). Dated sources sort
    # ascending; undated (NULL published_at) float to the END, not the top.
    _seed_entity(wiki_db, "e_x", "GraphRAG")
    upsert_source(
        wiki_db,
        _source(
            source_id="src_dated",
            content_key="k1",
            publication="Codrift",
            url="https://ex.com/d",
            published_at="2026-03-01",
        ),
    )
    upsert_source(
        wiki_db,
        _source(
            source_id="src_undated",
            content_key="k2",
            publication="Voidmag",
            url="https://ex.com/u",
            published_at=None,
        ),
    )
    insert_claim(
        wiki_db,
        _claim(
            claim_id="clm_undated", source_id="src_undated", text="Undated claim about GraphRAG."
        ),
    )
    insert_claim(
        wiki_db,
        _claim(
            claim_id="clm_dated",
            source_id="src_dated",
            text="Dated claim about GraphRAG.",
            provenance="source",
            stance="opinion",
        ),
    )
    insert_claim_entity(wiki_db, claim_id="clm_undated", entity_id="e_x")
    insert_claim_entity(wiki_db, claim_id="clm_dated", entity_id="e_x")
    wiki_db.commit()

    assert attributed_claims_for_entity(wiki_db, "e_x") == [
        AttributedClaim(
            text="Dated claim about GraphRAG.",
            provenance="source",
            stance="opinion",
            author="Jane Doe",
            publication="Codrift",
            published_at="2026-03-01",
            url="https://ex.com/d",
            title="A Title",
            fetched_at=NOW,
        ),
        AttributedClaim(
            text="Undated claim about GraphRAG.",
            provenance="source",
            stance="reported",
            author="Jane Doe",
            publication="Voidmag",
            published_at=None,
            url="https://ex.com/u",
            title="A Title",
            fetched_at=NOW,
        ),
    ]


# --- deterministic id minting (idempotent write path) ---


def test_mint_source_id_is_deterministic_over_content_key():
    key = "medium::https://example.com/a"
    minted = mint_source_id(key)
    assert minted == mint_source_id(key)
    assert minted.startswith("src_")
    assert mint_source_id("medium::https://example.com/b") != minted


def test_mint_claim_id_is_deterministic_and_scoped_to_source():
    # Same claim text in two different sources → distinct claim ids (the id is a
    # global PK), but stable across re-runs of the SAME (source, text) so a re-run
    # references the row insert_claim kept rather than a fresh, unlinked id.
    text_hash = claim_text_hash("Some claim.")
    a = mint_claim_id("src_aaa", text_hash)
    assert a == mint_claim_id("src_aaa", text_hash)
    assert a.startswith("clm_")
    assert mint_claim_id("src_bbb", text_hash) != a


# --- derived num_sources ---


def test_count_sources_for_entity_counts_distinct_sources(wiki_db):
    # num_sources(E) = COUNT(DISTINCT source) over E's claims — two claims from
    # one source and one from another count as 2, not 3.
    _seed_entity(wiki_db, "e_x", "GraphRAG")
    upsert_source(wiki_db, _source(source_id="src_a", content_key="ka"))
    upsert_source(wiki_db, _source(source_id="src_b", content_key="kb"))
    insert_claim(wiki_db, _claim(claim_id="c1", source_id="src_a", text="one"))
    insert_claim(wiki_db, _claim(claim_id="c2", source_id="src_a", text="two"))
    insert_claim(wiki_db, _claim(claim_id="c3", source_id="src_b", text="three"))
    for cid in ("c1", "c2", "c3"):
        insert_claim_entity(wiki_db, claim_id=cid, entity_id="e_x")
    wiki_db.commit()

    assert count_sources_for_entity(wiki_db, "e_x") == 2


def test_count_sources_for_entity_unknown_is_zero(wiki_db):
    assert count_sources_for_entity(wiki_db, "e_missing") == 0


# --- related (co-occurrence derived from claim_entities, not a stored ledger) ---


def _link(conn, *, source_id, content_key, claim_id, entity_id, text):
    """Seed one (source → claim → entity) link so co-occurrence can be derived."""
    upsert_source(conn, _source(source_id=source_id, content_key=content_key))
    insert_claim(conn, _claim(claim_id=claim_id, source_id=source_id, text=text))
    insert_claim_entity(conn, claim_id=claim_id, entity_id=entity_id)


def test_get_related_derives_co_occurrence_from_claim_entities(wiki_db):
    # Two entities claimed within the SAME source co-occur → each lists the other.
    # An entity that only appears in a DIFFERENT source shares no source → not related.
    for eid, name in [("e_rag", "RAG"), ("e_llm", "LLM"), ("e_solo", "Solo")]:
        _seed_entity(wiki_db, eid, name)
    _link(
        wiki_db, source_id="src_a", content_key="ka", claim_id="c1", entity_id="e_rag", text="one"
    )
    _link(
        wiki_db, source_id="src_a", content_key="ka", claim_id="c2", entity_id="e_llm", text="two"
    )
    _link(wiki_db, source_id="src_b", content_key="kb", claim_id="c3", entity_id="e_solo", text="x")
    wiki_db.commit()

    assert get_related_for_entity(wiki_db, "e_rag") == ["e_llm"]
    assert get_related_for_entity(wiki_db, "e_llm") == ["e_rag"]
    assert get_related_for_entity(wiki_db, "e_solo") == []


def test_get_related_ranks_by_distinct_source_count(wiki_db):
    # Strength = COUNT(DISTINCT source) co-mentioning the pair. e_llm shares TWO
    # sources with e_rag, e_chroma ONE → e_llm ranks first. Two claims about the
    # same pair in one source count once (no claim fan-out). Ties break entity_id ASC.
    for eid, name in [("e_rag", "RAG"), ("e_llm", "LLM"), ("e_chroma", "Chroma")]:
        _seed_entity(wiki_db, eid, name)
    # Source A: rag + llm co-mentioned (llm via two claims → still one source).
    _link(wiki_db, source_id="sa", content_key="ka", claim_id="a1", entity_id="e_rag", text="1")
    _link(wiki_db, source_id="sa", content_key="ka", claim_id="a2", entity_id="e_llm", text="2")
    _link(wiki_db, source_id="sa", content_key="ka", claim_id="a3", entity_id="e_llm", text="3")
    # Source B: rag + llm again → llm now shares 2 sources.
    _link(wiki_db, source_id="sb", content_key="kb", claim_id="b1", entity_id="e_rag", text="4")
    _link(wiki_db, source_id="sb", content_key="kb", claim_id="b2", entity_id="e_llm", text="5")
    # Source C: rag + chroma → chroma shares 1 source.
    _link(wiki_db, source_id="sc", content_key="kc", claim_id="c1", entity_id="e_rag", text="6")
    _link(wiki_db, source_id="sc", content_key="kc", claim_id="c2", entity_id="e_chroma", text="7")
    wiki_db.commit()

    assert get_related_for_entity(wiki_db, "e_rag") == ["e_llm", "e_chroma"]
    assert get_related_for_entity(wiki_db, "e_rag", limit=1) == ["e_llm"]


# --- attributed page render (markdown) ---


def test_render_attributed_markdown_includes_related():
    # `related` renders as an inline list in the frontmatter (producer-authoritative,
    # the co-occurring entity names get_related_for_entity derived).
    md = render_attributed_markdown(
        entity=_entity_record("e_x", "GraphRAG"),
        claims=[
            AttributedClaim(
                text="c",
                provenance="source",
                stance="reported",
                author=None,
                publication=None,
                published_at="2026-03-01",
                url="https://ex.com/a",
            )
        ],
        aliases=[],
        num_sources=1,
        updated_at="2026-07-03",
        related=["Microsoft", "RAG"],
    )
    assert "related: [Microsoft, RAG]" in md


def test_render_attributed_markdown_shape():
    # The page renders claims split into ## Reported / ## Opinion sections (the
    # header conveys the kind, so the inline tag is dropped), each attributed by
    # author · domain (date) — the memory hooks (specifics over abstractions).
    entity = _entity_record("e_x", "GraphRAG")
    claims = [
        AttributedClaim(
            text="GraphRAG combines vector search with a knowledge graph.",
            provenance="source",
            stance="reported",
            author="Jane Doe",
            publication=None,
            published_at="2026-03-01",
            url="https://medium.com/x",
        ),
        AttributedClaim(
            text="GraphRAG will replace naive RAG.",
            provenance="source",
            stance="opinion",
            author=None,
            publication=None,
            published_at=None,
            url="https://voidmag.com/y",
        ),
    ]
    md = render_attributed_markdown(
        entity=entity,
        claims=claims,
        aliases=["Graph RAG"],
        num_sources=2,
        updated_at="2026-07-02",
    )
    assert md == (
        "---\n"
        "entity_id: e_x\n"
        "title: GraphRAG\n"
        "entity_type: concept\n"
        "aliases: [Graph RAG]\n"
        "related: []\n"
        "summary: GraphRAG combines vector search with a knowledge graph.\n"
        "num_sources: 2\n"
        "updated_at: 2026-07-02\n"
        "---\n"
        "\n"
        "# GraphRAG\n"
        "\n"
        "## Reported\n"
        "\n"
        "- GraphRAG combines vector search with a knowledge graph. "
        "— Jane Doe · [medium.com](https://medium.com/x) (published 2026-03-01)\n"
        "\n"
        "## Opinion\n"
        "\n"
        "- GraphRAG will replace naive RAG. — [voidmag.com](https://voidmag.com/y)\n"
    )


def test_render_attributed_markdown_renders_domain_as_backlink():
    # Provenance: a source claim's domain renders as a real markdown backlink
    # `[domain](url)`, not bare text — so every claim links back to its origin.
    entity = _entity_record("e_x", "GraphRAG")
    claims = [
        AttributedClaim(
            text="GraphRAG combines vector search with a knowledge graph.",
            provenance="source",
            stance="reported",
            author="Jane Doe",
            publication=None,
            published_at="2026-03-01",
            url="https://medium.com/x",
        ),
    ]
    md = render_attributed_markdown(
        entity=entity, claims=claims, aliases=[], num_sources=1, updated_at="2026-07-02"
    )
    assert "[medium.com](https://medium.com/x)" in md


def test_render_attributed_markdown_shows_both_dates_labelled():
    # Both provenance dates surface as DISTINCT, labelled signals so a reader can
    # tell publish date from fetch date (recency reasoning needs them separate).
    entity = _entity_record("e_x", "GraphRAG")
    claims = [
        AttributedClaim(
            text="GraphRAG combines vector search with a knowledge graph.",
            provenance="source",
            stance="reported",
            author="Jane Doe",
            publication=None,
            published_at="2026-03-01",
            url="https://medium.com/x",
            fetched_at="2026-07-02",
        ),
    ]
    md = render_attributed_markdown(
        entity=entity, claims=claims, aliases=[], num_sources=1, updated_at="2026-07-02"
    )
    assert "(published 2026-03-01, fetched 2026-07-02)" in md


def test_render_attributed_markdown_no_publish_date_shows_only_fetched():
    # No publish date → show only the fetch date, labelled — NEVER substitute the
    # fetch date into the publish slot (that would launder a fake publish signal).
    entity = _entity_record("e_x", "GraphRAG")
    claims = [
        AttributedClaim(
            text="GraphRAG will replace naive RAG.",
            provenance="source",
            stance="opinion",
            author=None,
            publication=None,
            published_at=None,
            url="https://voidmag.com/y",
            fetched_at="2026-07-02",
        ),
    ]
    md = render_attributed_markdown(
        entity=entity, claims=claims, aliases=[], num_sources=1, updated_at="2026-07-02"
    )
    assert "(fetched 2026-07-02)" in md
    assert "published" not in md.split("## Opinion")[1]


def test_render_attributed_markdown_renders_derived_as_from_my_notes():
    # A promoted note is a `derived` claim — it renders under its own
    # `## From my notes` section (the user's own synthesis, kept separate from
    # source-side Reported/Opinion), with the note text verbatim.
    entity = _entity_record("e_n", "Agent harness")
    claims = [
        AttributedClaim(
            text="An agent harness wraps guardrails around a raw model.",
            provenance="user",
            stance=None,
            author=None,
            publication=None,
            published_at="2026-07-08",
            url=None,
        ),
    ]
    md = render_attributed_markdown(
        entity=entity, claims=claims, aliases=[], num_sources=1, updated_at="2026-07-10"
    )
    assert "## From my notes" in md
    assert "An agent harness wraps guardrails around a raw model." in md


def test_render_attributed_markdown_derived_backlinks_to_note_file():
    # A note-origin derived claim must link back to its origin note file, so it's
    # a traceable claim, not a "fake" unsourced assertion. The note title renders
    # as a backlink to the note file (carried on the claim's `url`).
    entity = _entity_record("e_n", "Agent harness")
    claims = [
        AttributedClaim(
            text="An agent harness wraps guardrails around a raw model.",
            provenance="user",
            stance=None,
            author=None,
            publication=None,
            published_at="2026-07-08",
            url="data/notes/2026-07-08_agent-harness-a1b2c3.md",
            title="Framework for AI Harness Design",
        ),
    ]
    md = render_attributed_markdown(
        entity=entity, claims=claims, aliases=[], num_sources=1, updated_at="2026-07-10"
    )
    assert (
        "[Framework for AI Harness Design]" "(data/notes/2026-07-08_agent-harness-a1b2c3.md)" in md
    )


def test_render_attributed_markdown_derived_keeps_block_structure():
    # A note is a structured artifact (headers, lists) — it renders as a verbatim
    # block, NOT flattened to a single bullet, so its line structure survives.
    entity = _entity_record("e_n", "Agent harness")
    note = "### Rubric\n\n1. Control mechanisms\n2. Context management"
    claims = [
        AttributedClaim(
            text=note,
            provenance="user",
            stance=None,
            author=None,
            publication=None,
            published_at="2026-07-08",
            url=None,
        ),
    ]
    md = render_attributed_markdown(
        entity=entity, claims=claims, aliases=[], num_sources=1, updated_at="2026-07-10"
    )
    assert note in md  # verbatim, newlines + list items intact
    assert "- ### Rubric" not in md  # not flattened to a bullet


def test_render_attributed_markdown_derived_attributes_note_title():
    # A note-origin derived claim attributes to the NOTE (its title + date), not
    # the NULL publication/author that would render "source unknown".
    entity = _entity_record("e_n", "Agent harness")
    claims = [
        AttributedClaim(
            text="An agent harness wraps guardrails around a raw model.",
            provenance="user",
            stance=None,
            author=None,
            publication=None,
            published_at="2026-07-08",
            url=None,
            title="Framework for AI Harness Design",
        ),
    ]
    md = render_attributed_markdown(
        entity=entity, claims=claims, aliases=[], num_sources=1, updated_at="2026-07-10"
    )
    assert "*Framework for AI Harness Design — my note, 2026-07-08*" in md
    assert "source unknown" not in md


def test_render_attributed_markdown_emits_deterministic_summary():
    # A `summary:` frontmatter field is emitted deterministically — the first
    # reported claim (the definitional lead), so the page carries a display line
    # AND non-empty embed text (the vector lane reads meta["summary"]).
    entity = _entity_record("e_x", "GraphRAG")
    claims = [
        AttributedClaim(
            text="GraphRAG combines vector search with a knowledge graph.",
            provenance="source",
            stance="reported",
            author="Jane Doe",
            publication=None,
            published_at="2026-03-01",
            url="https://medium.com/x",
        ),
        AttributedClaim(
            text="GraphRAG will replace naive RAG.",
            provenance="source",
            stance="opinion",
            author=None,
            publication=None,
            published_at=None,
            url="https://voidmag.com/y",
        ),
    ]
    md = render_attributed_markdown(
        entity=entity, claims=claims, aliases=[], num_sources=2, updated_at="2026-07-02"
    )
    assert "summary: GraphRAG combines vector search with a knowledge graph." in md


def test_render_attributed_markdown_summary_falls_back_to_note_when_derived_only():
    # A note that mints a fresh entity has NO reported/opinion claims — the summary
    # falls back to the note's first line (markdown header stripped), so a
    # derived-only page still embeds non-empty text.
    entity = _entity_record("e_n", "Agent harness")
    claims = [
        AttributedClaim(
            text="### Rubric\n\n1. Control mechanisms\n2. Context management",
            provenance="user",
            stance=None,
            author=None,
            publication=None,
            published_at="2026-07-08",
            url=None,
            title="AI harness design",
        ),
    ]
    md = render_attributed_markdown(
        entity=entity, claims=claims, aliases=[], num_sources=1, updated_at="2026-07-10"
    )
    assert "summary: Rubric" in md


def test_render_attributed_markdown_summary_skips_blank_claim_text():
    # A claim with empty/whitespace-only text must not crash the render sweep
    # (summary runs on every page): the blank claim is skipped and summary falls
    # through to the next usable claim.
    entity = _entity_record("e_x", "GraphRAG")
    claims = [
        AttributedClaim(
            text="   \n  ",
            provenance="source",
            stance="reported",
            author="A",
            publication=None,
            published_at="2026-01-01",
            url=None,
        ),
        AttributedClaim(
            text="GraphRAG combines vector search with a knowledge graph.",
            provenance="source",
            stance="reported",
            author="B",
            publication=None,
            published_at="2026-02-01",
            url=None,
        ),
    ]
    md = render_attributed_markdown(
        entity=entity, claims=claims, aliases=[], num_sources=1, updated_at="2026-07-02"
    )
    assert "summary: GraphRAG combines vector search with a knowledge graph." in md


def test_render_attributed_markdown_omits_empty_section():
    # An entity with only reported claims renders just the ## Reported section —
    # no empty ## Opinion header.
    entity = _entity_record("e_y", "RAG")
    claims = [
        AttributedClaim(
            text="RAG retrieves before generating.",
            provenance="source",
            stance="reported",
            author="A",
            publication=None,
            published_at="2026-01-01",
            url="https://medium.com/z",
        ),
    ]
    md = render_attributed_markdown(
        entity=entity, claims=claims, aliases=[], num_sources=1, updated_at="2026-07-02"
    )
    assert "## Reported" in md
    assert "## Opinion" not in md


def test_user_claim_roundtrips_with_provenance_and_no_stance(wiki_db):
    # A promoted note is authored BY THE USER, not produced by the pipeline.
    # `provenance` records who authored it; `stance` records how a SOURCE
    # presented it and is therefore meaningless here, so it stays NULL.
    upsert_source(wiki_db, _source())
    clm = _claim(text="I think harnesses matter more than models.", provenance="user", stance=None)
    insert_claim(wiki_db, clm)
    wiki_db.commit()
    stored = get_claims_for_source(wiki_db, "src_0000000000000001")[0]
    assert (stored.provenance, stored.stance) == ("user", None)


def test_pipeline_derived_claim_is_excluded_loudly_not_silently(caplog):
    # `provenance='derived'` (a pipeline merge of other claims) has no render
    # section: "From my notes" is for the user's own writing, and Reported /
    # Opinion select on `stance`, which a derived claim does not have. Nothing
    # emits these yet, so rather than invent a section for a shape with no
    # producer, they are excluded — but a claim that is in the DB and on no page
    # is silent data loss, so the exclusion must announce itself.
    entity = _entity_record("e_x", "GraphRAG")
    claims = [
        AttributedClaim(
            text="Merged: GraphRAG adoption is rising across both papers.",
            provenance="derived",
            stance=None,
            author=None,
            publication=None,
            published_at=None,
            url=None,
        ),
    ]
    with caplog.at_level("WARNING"):
        md = render_attributed_markdown(
            entity=entity, claims=claims, aliases=[], num_sources=1, updated_at="2026-08-24"
        )
    assert "Merged: GraphRAG adoption" not in md
    assert "e_x" in caplog.text and "derived" in caplog.text
