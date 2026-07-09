"""End-to-end guard for KP-2 — materialize the promote_notes ASSET (real
WikiResource + NotesResource, on-disk wiki.db + notes dir) and drive the derived
claim through the downstream it must survive:

  materialize(promote_notes) -> derived claim lands on the resolved entity
  -> merge that entity away -> the derived claim survives on the survivor
  -> render_entity_pages + build_wiki_index run clean with a derived claim present

Render is not derived-aware yet (KP-3), so this asserts the chain INTEGRITY (no
crash, index builds), not a "From my notes" section.
"""

import dagster as dg
from domains.wiki.attributed import attributed_claims_for_entity
from domains.wiki.identity import EntityRecord, normalize_name, slugify
from domains.wiki.state import connection, insert_entity, merge_entities
from orchestrators.defs.shared.resources import NotesResource, WikiResource
from orchestrators.defs.synthesize_wiki.assets import promote_notes
from workflows.wiki_synthesis.attributed_synthesis import render_entity_pages
from workflows.wiki_synthesis.wiki_index import build_wiki_index

NOW = "2026-07-09T00:00:00+00:00"


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
