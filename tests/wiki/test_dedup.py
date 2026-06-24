"""Tests for domains.wiki.dedup — the offline merge-candidate generator (#15).

`find_merge_candidates` is pure over an injected `embed_batch` callable, so it's
tested with a fake embedder (no OpenAI). `load_entity_texts` reads entities + the
summary from each page's `.md` frontmatter.
"""

from datetime import date

import pytest
from domains.wiki.dedup import CandidatePair, EntityText, find_merge_candidates, load_entity_texts
from domains.wiki.identity import EntityRecord, normalize_name, slugify
from domains.wiki.io import write_page
from domains.wiki.state import connection, insert_entity, upsert_page
from domains.wiki.types import WikiPage

NOW = "2026-06-22T00:00:00+00:00"


def _fake_embed(table):
    def embed_batch(texts):
        return [table[t] for t in texts]

    return embed_batch


def test_find_merge_candidates_returns_high_cosine_pairs_sorted():
    items = [
        EntityText("e_a", "Claude Max", "Anthropic subscription tier"),
        EntityText("e_b", "Max plan", "Anthropic subscription tier"),
        EntityText("e_c", "Chroma", "an open-source vector database"),
    ]
    embed = _fake_embed(
        {
            "Claude Max\nAnthropic subscription tier": [1.0, 0.0],
            "Max plan\nAnthropic subscription tier": [0.99, 0.02],
            "Chroma\nan open-source vector database": [0.0, 1.0],
        }
    )

    pairs = find_merge_candidates(items, embed, threshold=0.8)

    assert [(p.a.entity_id, p.b.entity_id) for p in pairs] == [("e_a", "e_b")]
    assert pairs[0].score > 0.9
    assert isinstance(pairs[0], CandidatePair)


def test_find_merge_candidates_orders_by_score_then_tie_breaks_deterministically():
    items = [
        EntityText("e_a", "A", "x"),
        EntityText("e_b", "B", "x"),
        EntityText("e_c", "C", "x"),
    ]
    embed = _fake_embed(
        {
            "A\nx": [1.0, 0.0],
            "B\nx": [1.0, 0.0],  # cosine(A,B) = 1.0
            "C\nx": [0.9, 0.43589],  # cosine(A,C) = cosine(B,C) = 0.9
        }
    )

    pairs = find_merge_candidates(items, embed, threshold=0.85)

    assert [(p.a.entity_id, p.b.entity_id) for p in pairs] == [
        ("e_a", "e_b"),  # 1.0
        ("e_a", "e_c"),  # 0.9, tie-break a then b
        ("e_b", "e_c"),  # 0.9
    ]


def test_find_merge_candidates_empty_for_fewer_than_two():
    assert find_merge_candidates([], _fake_embed({})) == []
    assert find_merge_candidates([EntityText("e", "N", "s")], _fake_embed({})) == []


def test_find_merge_candidates_orientation_is_input_order_independent():
    a = EntityText("e_a", "A", "x")
    b = EntityText("e_b", "B", "x")
    embed = _fake_embed({"A\nx": [1.0, 0.0], "B\nx": [1.0, 0.0]})

    fwd = find_merge_candidates([a, b], embed, threshold=0.8)
    rev = find_merge_candidates([b, a], embed, threshold=0.8)

    assert fwd == rev
    assert (fwd[0].a.entity_id, fwd[0].b.entity_id) == ("e_a", "e_b")  # canonical: a < b


def test_find_merge_candidates_rejects_misaligned_embeddings():
    items = [EntityText("e_a", "A", "x"), EntityText("e_b", "B", "x")]
    with pytest.raises(ValueError, match="embedding count"):
        find_merge_candidates(items, lambda texts: [[1.0, 0.0]], threshold=0.8)  # 1 vec, 2 items


def _seed(conn, wiki_dir, entity_id, name, summary, file_name):
    insert_entity(
        conn,
        EntityRecord(
            entity_id=entity_id,
            canonical_name=name,
            normalized_name=normalize_name(name),
            slug=slugify(name),
            page_type="concept",
            created_at=NOW,
        ),
    )
    upsert_page(conn, entity_id=entity_id, file_path=file_name, related_ids=[])
    write_page(
        wiki_dir / file_name,
        WikiPage(
            entity_id=entity_id,
            title=name,
            page_type="concept",
            summary=summary,
            related=[],
            sources=["art1"],
            updated_at=date(2026, 6, 22),
            content=f"# {name}\n\nbody",
        ),
        aliases=[],
        num_sources=1,
        sources=["art1"],
        related=[],
    )


def test_load_entity_texts_reads_name_and_md_summary(tmp_path, wiki_db_path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    with connection(wiki_db_path) as conn, conn:
        _seed(conn, wiki_dir, "e_rag", "RAG", "Retrieval-augmented generation.", "rag-1.md")
        _seed(conn, wiki_dir, "e_chroma", "Chroma", "A vector database.", "chroma-2.md")

    with connection(wiki_db_path) as conn:
        texts = load_entity_texts(conn, wiki_dir)

    by_id = {t.entity_id: t for t in texts}
    assert by_id["e_rag"] == EntityText("e_rag", "RAG", "Retrieval-augmented generation.")
    assert by_id["e_chroma"].summary == "A vector database."


def test_load_entity_texts_tolerates_missing_page_file(tmp_path, wiki_db_path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    with connection(wiki_db_path) as conn, conn:
        _seed(conn, wiki_dir, "e_rag", "RAG", "RAG summary.", "rag-1.md")
        (wiki_dir / "rag-1.md").unlink()  # page row exists, file gone

    with connection(wiki_db_path) as conn:
        texts = load_entity_texts(conn, wiki_dir)

    assert texts == [EntityText("e_rag", "RAG", "")]
