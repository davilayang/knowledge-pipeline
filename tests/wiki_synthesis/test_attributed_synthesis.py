"""Tests for the attributed-lane synthesis orchestration — per-source synthesize
(queue row → SourceRecord → assign → persist) and the entity-page render sweep.

Uses the `wiki_db` / `wiki_db_path` fixtures (fresh SQLite wiki.db, schema
applied).
"""

from domains.wiki.attributed import (
    ClaimRecord,
    SourceRecord,
    attributed_claims_for_entity,
    claim_text_hash,
    insert_claim,
    insert_claim_entity,
    mint_claim_id,
    mint_source_id,
    upsert_source,
)
from domains.wiki.identity import EntityRecord, normalize_name, shortid, slugify
from domains.wiki.state import connection, get_all_entities, get_page, insert_entity
from workflows.wiki_synthesis.attributed_synthesis import (
    build_source_record,
    render_entity_pages,
    synthesize_source,
)

NOW = "2026-07-02T00:00:00+00:00"

_CLAIMS_DOC = (
    "---\n"
    "item_id: https://medium.com/x\n"
    "content_date: '2026-03-01'\n"
    "---\n"
    "\n"
    "- [reported] GraphRAG uses a knowledge graph.\n"
)
# Two claims from one source — clears the page-worthiness floor (≥2 claims).
_CLAIMS_DOC_TWO = (
    "---\n"
    "item_id: https://medium.com/x\n"
    "content_date: '2026-03-01'\n"
    "---\n"
    "\n"
    "- [reported] GraphRAG uses a knowledge graph.\n"
    "- [opinion] GraphRAG will replace naive RAG.\n"
)
_CANDIDATES_DOC = "GraphRAG — concept\n"


def _source():
    return build_source_record(
        {
            "url": "https://medium.com/x",
            "canonical_url": "https://medium.com/x",
            "title": "T",
            "author": "Jane Doe",
            "content_date": "2026-03-01",
            "content_hash": "h",
        },
        now=NOW,
    )


def test_build_source_record_maps_queue_row():
    # A queue_items row (SELECT * dict) maps to a wiki SourceRecord: content_key
    # is the normalized canonical_url, origin_type is 'queue', published_at is
    # the row's content_date, and the source_id is minted from content_key.
    row = {
        "notion_page_id": "pg1",
        "title": "A Title",
        "author": "Jane Doe",
        "content_date": "2026-03-01",
        "url": "https://medium.com/x?utm=1",
        "canonical_url": "https://medium.com/x",
        "content_hash": "h1",
    }
    src = build_source_record(row, now=NOW)

    assert src.content_key == "https://medium.com/x"
    assert src.source_id == mint_source_id("https://medium.com/x")
    assert src.origin_type == "queue"
    assert src.title == "A Title"
    assert src.author == "Jane Doe"
    assert src.publication is None
    assert src.url == "https://medium.com/x?utm=1"
    assert src.published_at == "2026-03-01"
    assert src.content_hash == "h1"
    assert src.added_at == NOW


def test_build_source_record_falls_back_to_url_when_no_canonical():
    row = {"notion_page_id": "pg2", "url": "https://medium.com/y", "canonical_url": None}
    src = build_source_record(row, now=NOW)
    assert src.content_key == "https://medium.com/y"


def test_synthesize_source_persists_attributed_claims(wiki_db_path):
    # Reads the source's stored claim + candidate docs, resolves/mints the entity
    # against the (empty) wiki, and persists the attributed claim. The single
    # unambiguous mention needs no LLM (attribute_subjects unused).
    source_id = synthesize_source(
        claims_doc=_CLAIMS_DOC,
        candidates_doc=_CANDIDATES_DOC,
        source=_source(),
        wiki_db_path=wiki_db_path,
    )
    assert source_id == mint_source_id("https://medium.com/x")

    with connection(wiki_db_path) as conn:
        entities = get_all_entities(conn)
        assert [e.canonical_name for e in entities] == ["GraphRAG"]
        claims = attributed_claims_for_entity(conn, entities[0].entity_id)
        assert [c.text for c in claims] == ["GraphRAG uses a knowledge graph."]


def test_render_entity_pages_skips_below_worthiness_floor(tmp_path, wiki_db_path):
    # One claim from one source is a passing co-mention — below the floor
    # (≥2 claims OR ≥2 sources), so no page is written.
    synthesize_source(
        claims_doc=_CLAIMS_DOC,
        candidates_doc=_CANDIDATES_DOC,
        source=_source(),
        wiki_db_path=wiki_db_path,
    )
    wiki_dir = tmp_path / "wiki"
    rendered = render_entity_pages(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)
    assert rendered == []
    assert not (wiki_dir.exists() and list(wiki_dir.glob("*.md")))


def test_render_prunes_page_when_entity_drops_below_floor(tmp_path, wiki_db_path):
    # Claim replacement can shrink an entity below the page-worthiness floor: a page
    # built from 2 claims, then re-extracted down to 1, is no longer page-worthy.
    # Render must DELETE the stale page (row + .md), not leave it behind.
    wiki_dir = tmp_path / "wiki"
    synthesize_source(
        claims_doc=_CLAIMS_DOC_TWO,
        candidates_doc=_CANDIDATES_DOC,
        source=_source(),
        wiki_db_path=wiki_db_path,
    )
    assert render_entity_pages(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)
    assert list(wiki_dir.glob("*.md"))  # page written

    # Re-extract with ONE claim (replacement) → 1 claim / 1 source → below floor.
    synthesize_source(
        claims_doc=_CLAIMS_DOC,
        candidates_doc=_CANDIDATES_DOC,
        source=_source(),
        wiki_db_path=wiki_db_path,
    )
    render_entity_pages(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)

    assert list(wiki_dir.glob("*.md")) == []
    with connection(wiki_db_path) as conn:
        ent = get_all_entities(conn)[0]
        assert get_page(conn, ent.entity_id) is None


_CLAIMS_DOC_CO = (
    "---\n"
    "item_id: https://medium.com/x\n"
    "content_date: '2026-03-01'\n"
    "---\n"
    "\n"
    "- [reported] GraphRAG uses a knowledge graph.\n"
    "- [reported] GraphRAG improves retrieval quality.\n"
    "- [reported] Microsoft released the tool.\n"
    "- [opinion] Microsoft invests heavily in research.\n"
)
_CANDIDATES_DOC_CO = "GraphRAG — concept\nMicrosoft — organization\n"


def test_render_writes_related_co_occurrence(tmp_path, wiki_db_path):
    # Two page-worthy entities co-mentioned in one source → each page's `related`
    # names the other (co-occurrence derived from claim_entities), and the pages
    # row records the neighbour ids (no longer hardcoded empty).
    synthesize_source(
        claims_doc=_CLAIMS_DOC_CO,
        candidates_doc=_CANDIDATES_DOC_CO,
        source=_source(),
        wiki_db_path=wiki_db_path,
    )
    wiki_dir = tmp_path / "wiki"
    render_entity_pages(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)

    with connection(wiki_db_path) as conn:
        by_name = {e.canonical_name: e for e in get_all_entities(conn)}
        gr, ms = by_name["GraphRAG"], by_name["Microsoft"]
        assert get_page(conn, gr.entity_id).related_ids == [ms.entity_id]
        assert get_page(conn, ms.entity_id).related_ids == [gr.entity_id]

    gr_md = (wiki_dir / f"{gr.slug}-{shortid(gr.entity_id)}.md").read_text(encoding="utf-8")
    assert "related: [Microsoft]" in gr_md


def test_render_entity_pages_writes_page_and_upserts(tmp_path, wiki_db_path):
    synthesize_source(
        claims_doc=_CLAIMS_DOC_TWO,
        candidates_doc=_CANDIDATES_DOC,
        source=_source(),
        wiki_db_path=wiki_db_path,
    )
    wiki_dir = tmp_path / "wiki"
    rendered = render_entity_pages(
        wiki_db_path=wiki_db_path, wiki_dir=wiki_dir, updated_at="2026-07-02"
    )

    with connection(wiki_db_path) as conn:
        ent = get_all_entities(conn)[0]
        page = get_page(conn, ent.entity_id)
    filename = f"{ent.slug}-{shortid(ent.entity_id)}.md"

    assert rendered == [ent.entity_id]
    assert page.file_path == filename
    text = (wiki_dir / filename).read_text(encoding="utf-8")
    assert "# GraphRAG" in text
    assert "## Reported" in text
    assert "- GraphRAG uses a knowledge graph. — Jane Doe · medium.com (2026-03-01)" in text


def _seed_lone_derived(conn, *, entity_id, name, note_title, body):
    """Seed a fresh concept entity carrying ONE derived claim from ONE note
    source — the shape a note that mints a new entity produces."""
    insert_entity(
        conn,
        EntityRecord(
            entity_id=entity_id,
            canonical_name=name,
            normalized_name=normalize_name(name),
            slug=slugify(name),
            entity_type="concept",
            created_at=NOW,
        ),
    )
    ck = f"local:{note_title}"
    sid = mint_source_id(ck)
    upsert_source(
        conn,
        SourceRecord(
            source_id=sid,
            content_key=ck,
            origin_type="note",
            title=note_title,
            author=None,
            publication=None,
            url=None,
            published_at="2026-07-08",
            content_hash=None,
            fetched_at=None,
            added_at=NOW,
        ),
    )
    th = claim_text_hash(body)
    cid = insert_claim(
        conn,
        ClaimRecord(
            claim_id=mint_claim_id(sid, th),
            source_id=sid,
            text=body,
            text_hash=th,
            claim_kind="derived",
            created_at=NOW,
        ),
    )
    insert_claim_entity(conn, claim_id=cid, entity_id=entity_id)


def test_render_entity_pages_keeps_lone_derived_entity(tmp_path, wiki_db_path):
    # A promoted note that mints a fresh entity leaves it with ONE derived claim
    # from ONE note source — below the source-side floor (≥2 claims / ≥2 sources).
    # A note is page-worthy on its own, so the page must still render.
    eid = "e_lonederived01"
    with connection(wiki_db_path) as conn, conn:
        _seed_lone_derived(
            conn, entity_id=eid, name="Agent harness", note_title="My note", body="A harness body."
        )
    wiki_dir = tmp_path / "wiki"
    rendered = render_entity_pages(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir, entity_ids=[eid])

    assert rendered == [eid]
    with connection(wiki_db_path) as conn:
        assert get_page(conn, eid) is not None
