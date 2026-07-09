"""End-to-end guard for KP-2 — materialize the promote_notes ASSET (real
WikiResource + NotesResource, on-disk wiki.db + notes dir) and drive the derived
claim through the downstream it must survive:

  materialize(promote_notes) -> derived claim lands on the resolved entity
  -> merge that entity away -> the derived claim survives on the survivor
  -> render_entity_pages + build_wiki_index run clean with a derived claim present

Render is not derived-aware yet (KP-3), so this asserts the chain INTEGRITY (no
crash, index builds), not a "From my notes" section.
"""

from pathlib import Path

import dagster as dg
from domains.wiki.attributed import (
    ClaimRecord,
    SourceRecord,
    attributed_claims_for_entity,
    claim_text_hash,
    count_sources_for_entity,
    insert_claim,
    insert_claim_entity,
    mint_claim_id,
    mint_source_id,
    upsert_source,
)
from domains.wiki.identity import EntityRecord, normalize_name, slugify
from domains.wiki.state import (
    connection,
    get_all_entities,
    get_page,
    insert_entity,
    merge_entities,
)
from orchestrators.defs.shared.resources import NotesResource, WikiResource
from orchestrators.defs.synthesize_wiki.assets import promote_notes
from workflows.wiki_synthesis.attributed_synthesis import render_entity_pages
from workflows.wiki_synthesis.wiki_index import build_wiki_index

NOW = "2026-07-09T00:00:00+00:00"
FIXTURES = Path(__file__).parent / "fixtures"


def _seed(conn, entity_id, name):
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


def _seed_source_claim(conn, entity_id, *, content_key, publication, text):
    """Give an entity a reported claim from a real article source — two of these
    make it page-worthy (>=2 sources), so a note can then enrich a rendered page."""
    upsert_source(
        conn,
        SourceRecord(
            source_id=mint_source_id(content_key),
            content_key=content_key,
            origin_type="queue",
            title="Article",
            author="Reporter",
            publication=publication,
            url=f"https://ex.com/{content_key[-1]}",
            published_at="2026-03-01",
            content_hash="h",
            fetched_at=NOW,
            added_at=NOW,
        ),
    )
    th = claim_text_hash(text)
    cid = insert_claim(
        conn,
        ClaimRecord(
            mint_claim_id(mint_source_id(content_key), th),
            mint_source_id(content_key),
            text,
            th,
            "reported",
            NOW,
        ),
    )
    insert_claim_entity(conn, claim_id=cid, entity_id=entity_id)


def _derived_texts(conn, entity_id):
    return [
        c.text for c in attributed_claims_for_entity(conn, entity_id) if c.claim_kind == "derived"
    ]


def test_promote_notes_asset_lands_and_survives_merge(tmp_path):
    wiki = WikiResource(
        backup_dir=str(tmp_path),
        wiki_dir=str(tmp_path / "wiki"),
        wiki_db_path=str(tmp_path / "wiki.db"),
    )
    notes = NotesResource(backup_source_dir=str(tmp_path))
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()

    db_path = wiki.get_db_path()  # applies schema
    with connection(db_path) as conn, conn:
        _seed(conn, "e_000000000000000a", "Agent Harness")
        _seed(conn, "e_000000000000000b", "Agentic Harness")  # merge target
    (notes_dir / "2026-07-08_note.md").write_text(
        "---\ntitle: Note\nentities: [Agent Harness]\npromote: true\n"
        "updated_at: '2026-07-08T16:51:00+00:00'\n---\n\nMy durable synthesis.\n"
    )

    result = dg.materialize([promote_notes], resources={"wiki": wiki, "notes": notes})
    assert result.success

    with connection(db_path) as conn:
        assert _derived_texts(conn, "e_000000000000000a") == ["My durable synthesis."]

    # Merge the noted entity into another — the derived claim must ride the
    # claim_entities repoint onto the survivor (a KP-2 "survives merge" guarantee).
    with connection(db_path) as conn, conn:
        merge_entities(conn, keep_id="e_000000000000000b", drop_id="e_000000000000000a")
    with connection(db_path) as conn:
        assert _derived_texts(conn, "e_000000000000000b") == ["My durable synthesis."]

    # The downstream render + index run clean with a derived claim present.
    render_entity_pages(wiki_db_path=db_path, wiki_dir=wiki.get_wiki_dir())
    r = build_wiki_index(wiki_db_path=db_path, wiki_dir=wiki.get_wiki_dir())
    assert (wiki.get_wiki_dir() / "_index" / "resolve.json").exists()
    assert r.pages_total >= 0


def test_real_note_fixture_enriches_a_page(tmp_path):
    # Full realistic flow with a real prod note (Tejas Kumar's AI harness rubric).
    # "AI harness design" (the note's primary hint, entities[0]) already exists as
    # a page-worthy entity from prior reading; promoting the note enriches it — the
    # note-source bumps num_sources and its co-hints feed `related` — and mints the
    # note's other hints (incl. a named person, Tejas Kumar) as new entities.
    wiki = WikiResource(
        backup_dir=str(tmp_path),
        wiki_dir=str(tmp_path / "wiki"),
        wiki_db_path=str(tmp_path / "wiki.db"),
    )
    notes = NotesResource(backup_source_dir=str(tmp_path))
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    fixture = FIXTURES / "2026-07-07_framework-for-ai-harness-design-203820.md"
    (notes_dir / fixture.name).write_text(fixture.read_text())

    primary = "e_00000000000000a1"
    db_path = wiki.get_db_path()
    with connection(db_path) as conn, conn:
        _seed(conn, primary, "AI harness design")  # already known from reading
        _seed_source_claim(
            conn,
            primary,
            content_key="q::a",
            publication="Latent Space",
            text="Harnesses wrap black-box LLMs in reliability layers.",
        )
        _seed_source_claim(
            conn,
            primary,
            content_key="q::b",
            publication="The Batch",
            text="Deterministic handlers offload sensitive steps from the model.",
        )

    result = dg.materialize([promote_notes], resources={"wiki": wiki, "notes": notes})
    assert result.success
    render_entity_pages(wiki_db_path=db_path, wiki_dir=wiki.get_wiki_dir())
    build_wiki_index(wiki_db_path=db_path, wiki_dir=wiki.get_wiki_dir())

    with connection(db_path) as conn:
        names = {e.normalized_name for e in get_all_entities(conn)}
        # The note's derived synthesis landed on the primary entity...
        assert any("rubric to guide harness design" in t for t in _derived_texts(conn, primary))
        # ...its note-source bumped the source count (2 articles + the note)...
        assert count_sources_for_entity(conn, primary) == 3
        # ...and its other hints were minted, including the named person.
        assert {"tejas kumar", "deterministic handlers", "outcome verification"} <= names
        page = wiki.get_wiki_dir() / get_page(conn, primary).file_path

    body = page.read_text()
    assert "num_sources: 3" in body
    assert "deterministic handlers" in body  # a co-hint surfaced in `related`

    # Show the rendered page (pytest -s) so the output is inspectable.
    print("\n" + "=" * 70 + f"\nRENDERED WIKI PAGE — {page.name}\n" + "=" * 70)
    print(body)
