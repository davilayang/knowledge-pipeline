"""Tests for the `wiki-dedup-candidates` CLI glue (evals.wiki_dedup).

`run_candidates` wires the domains reader + pure candidate search; it takes an
injected `embed_batch` so it's tested end-to-end against a real temp wiki.db
without OpenAI. `main` wraps it with an `OpenAIEmbedder`.
"""

import json
from datetime import date

import pytest
from domains.wiki.dedup import EntityText
from domains.wiki.identity import EntityRecord, normalize_name, slugify
from domains.wiki.io import write_page
from domains.wiki.state import connection, insert_entity, upsert_page
from domains.wiki.types import WikiPage
from evals.wiki_dedup import (
    embed_batch_with_prefix,
    fetch_entity_texts_via_datasette,
    pairs_to_json,
    run_candidates,
)

NOW = "2026-06-22T00:00:00+00:00"


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


def test_run_candidates_finds_near_dup_pair(tmp_path, wiki_db_path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    with connection(wiki_db_path) as conn, conn:
        _seed(conn, wiki_dir, "e_max", "Claude Max", "Anthropic tier.", "claude-max.md")
        _seed(conn, wiki_dir, "e_plan", "Max plan", "Anthropic tier.", "max-plan.md")
        _seed(conn, wiki_dir, "e_chroma", "Chroma", "Vector database.", "chroma.md")

    vecs = {
        "Claude Max\nAnthropic tier.": [1.0, 0.0],
        "Max plan\nAnthropic tier.": [0.98, 0.04],
        "Chroma\nVector database.": [0.0, 1.0],
    }

    pairs = run_candidates(
        lambda texts: [vecs[t] for t in texts],
        db_path=wiki_db_path,
        wiki_dir=wiki_dir,
        threshold=0.8,
    )

    assert [(p.a.entity_id, p.b.entity_id) for p in pairs] == [("e_max", "e_plan")]


class _FakeResp:
    def __init__(self, data: bytes):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._data


def test_fetch_entity_texts_via_datasette_parses_rows():
    rows = [
        {"entity_id": "e_max", "canonical_name": "Claude Max", "summary": "Anthropic tier."},
        {"entity_id": "e_x", "canonical_name": "Chroma", "summary": None},  # no page version
    ]
    seen: list[str] = []

    def fake_opener(url):
        seen.append(url)
        return _FakeResp(json.dumps(rows).encode())

    items = fetch_entity_texts_via_datasette("https://host/databases/wiki", opener=fake_opener)

    assert items == [
        EntityText("e_max", "Claude Max", "Anthropic tier."),
        EntityText("e_x", "Chroma", ""),  # NULL summary → ""
    ]
    assert seen[0].startswith("https://host/databases/wiki.json?")
    assert "sql=" in seen[0]


def test_fetch_entity_texts_via_datasette_rejects_error_shaped_json():
    # Datasette returns an error object (not an array) when custom SQL is
    # disabled / auth fails — fail loud, not with a cryptic KeyError.
    def fake_opener(url):
        return _FakeResp(json.dumps({"error": "custom SQL not allowed", "ok": False}).encode())

    with pytest.raises(ValueError, match="array"):
        fetch_entity_texts_via_datasette("https://host/databases/wiki", opener=fake_opener)


def test_run_candidates_reads_from_datasette_when_url_given(monkeypatch):
    items = [
        EntityText("e_max", "Claude Max", "Anthropic tier."),
        EntityText("e_plan", "Max plan", "Anthropic tier."),
        EntityText("e_chroma", "Chroma", "Vector database."),
    ]
    monkeypatch.setattr("evals.wiki_dedup.fetch_entity_texts_via_datasette", lambda url: items)
    vecs = {
        "Claude Max\nAnthropic tier.": [1.0, 0.0],
        "Max plan\nAnthropic tier.": [0.98, 0.04],
        "Chroma\nVector database.": [0.0, 1.0],
    }

    pairs = run_candidates(
        lambda texts: [vecs[t] for t in texts],
        datasette_url="https://host/databases/wiki",
        threshold=0.8,
    )

    assert [(p.a.entity_id, p.b.entity_id) for p in pairs] == [("e_max", "e_plan")]


def test_embed_batch_with_prefix_prepends_to_each_text():
    seen: list[str] = []

    def fake(texts):
        seen.extend(texts)
        return [[1.0] for _ in texts]

    wrapped = embed_batch_with_prefix(fake, "search_document: ")
    wrapped(["RAG\nfoo", "Chroma\nbar"])
    assert seen == ["search_document: RAG\nfoo", "search_document: Chroma\nbar"]


def test_embed_batch_with_prefix_is_passthrough_when_empty():
    def fake(texts):
        return [[1.0] for _ in texts]

    assert embed_batch_with_prefix(fake, "") is fake


def test_pairs_to_json_is_judging_friendly(tmp_path, wiki_db_path):
    from domains.wiki.dedup import CandidatePair, EntityText

    pair = CandidatePair(
        a=EntityText("e_max", "Claude Max", "Anthropic tier."),
        b=EntityText("e_plan", "Max plan", "Anthropic tier."),
        score=0.991,
    )
    payload = json.loads(pairs_to_json([pair]))
    assert payload == [
        {
            "score": 0.991,
            "a": {"entity_id": "e_max", "name": "Claude Max", "summary": "Anthropic tier."},
            "b": {"entity_id": "e_plan", "name": "Max plan", "summary": "Anthropic tier."},
        }
    ]
