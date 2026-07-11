"""Tests for the promote_notes workflow (KP-2 slices 1-8).

Drives the public `promote_notes(db_path, notes_dir)` entry and asserts through
the attributed read layer (`attributed_claims_for_entity`) — a promoted note
becomes a `derived` claim on the resolved canonical entity. Uses the `wiki_db`
fixture (seed/inspect) + `wiki_db_path` (the workflow opens its own connection).
"""

from pathlib import Path

from domains.wiki.attributed import (
    attributed_claims_for_entity,
    get_source,
    mint_source_id,
)
from domains.wiki.identity import EntityRecord, normalize_name, slugify
from domains.wiki.state import insert_entity
from workflows.wiki_synthesis.promote_notes import promote_notes

NOW = "2026-07-09T00:00:00+00:00"


def _seed_entity(conn, entity_id, name) -> str:
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
    conn.commit()
    return entity_id


def _write_note(notes_dir: Path, note_id: str, *, entities, body="My synthesis.", promote=True):
    notes_dir.mkdir(exist_ok=True)
    hints = "[" + ", ".join(entities) + "]"
    (notes_dir / f"{note_id}.md").write_text(
        f"---\ntitle: {note_id}\ndate: '2026-07-08'\n"
        f"entities: {hints}\npromote: {str(promote).lower()}\n"
        f"updated_at: '2026-07-08T16:51:00+00:00'\n---\n\n{body}\n"
    )


def _derived_texts(conn, entity_id) -> list[str]:
    return [
        c.text for c in attributed_claims_for_entity(conn, entity_id) if c.claim_kind == "derived"
    ]


def test_promoted_note_lands_derived_claim_on_existing_entity(wiki_db, wiki_db_path, tmp_path):
    eid = _seed_entity(wiki_db, "e_0000000000000001", "Agent Harness")
    notes = tmp_path / "notes"
    _write_note(
        notes, "2026-07-08_note-a1", entities=["Agent Harness"], body="Harness = a DX engine."
    )

    promote_notes(db_path=wiki_db_path, notes_dir=notes)

    assert _derived_texts(wiki_db, eid) == ["Harness = a DX engine."]


def test_note_source_backlinks_to_note_file(wiki_db, wiki_db_path, tmp_path):
    # A promoted note's source must backlink to its origin note file (else its
    # derived claim is an untraceable "fake" assertion). The backlink is the
    # stable note-file path, keyed on the note_id.
    _seed_entity(wiki_db, "e_0000000000000001", "Agent Harness")
    notes = tmp_path / "notes"
    _write_note(notes, "2026-07-08_note-a1", entities=["Agent Harness"])

    promote_notes(db_path=wiki_db_path, notes_dir=notes)

    src = get_source(wiki_db, mint_source_id("local:2026-07-08_note-a1"))
    assert src.url == "data/notes/2026-07-08_note-a1.md"


def test_two_notes_same_new_concept_land_on_one_entity(wiki_db, wiki_db_path, tmp_path):
    # Two notes in one tick hint the same NOT-yet-existing concept — batch
    # resolution mints it once, not one entity per note (no fragmentation).
    notes = tmp_path / "notes"
    _write_note(notes, "note-a", entities=["Prompt Caching"], body="A.")
    _write_note(notes, "note-b", entities=["prompt caching"], body="B.")  # same, different case

    promote_notes(db_path=wiki_db_path, notes_dir=notes)

    from domains.wiki.state import get_all_entities

    matches = [e for e in get_all_entities(wiki_db) if e.normalized_name == "prompt caching"]
    assert len(matches) == 1
    assert sorted(_derived_texts(wiki_db, matches[0].entity_id)) == ["A.", "B."]


def test_hint_resolves_to_merged_away_survivor_via_alias(wiki_db, wiki_db_path, tmp_path):
    # A merged entity leaves its old name as an alias of the survivor. A note
    # hinting the old name must land on the survivor, not resurrect the merged one.
    survivor = _seed_entity(wiki_db, "e_0000000000000009", "Agentic Harness")
    wiki_db.execute(
        "INSERT INTO aliases (alias, normalized_alias, entity_id) VALUES (?, ?, ?)",
        ("Agent Harness", "agent harness", survivor),
    )
    wiki_db.commit()
    notes = tmp_path / "notes"
    _write_note(notes, "note-x", entities=["Agent Harness"], body="Old-name note.")

    promote_notes(db_path=wiki_db_path, notes_dir=notes)

    from domains.wiki.state import get_all_entities

    assert [e.entity_id for e in get_all_entities(wiki_db)] == [survivor]  # nothing resurrected
    assert _derived_texts(wiki_db, survivor) == ["Old-name note."]


def test_rerun_is_idempotent(wiki_db, wiki_db_path, tmp_path):
    eid = _seed_entity(wiki_db, "e_0000000000000001", "Agent Harness")
    notes = tmp_path / "notes"
    _write_note(notes, "note-a1", entities=["Agent Harness"], body="Stable body.")

    promote_notes(db_path=wiki_db_path, notes_dir=notes)
    promote_notes(db_path=wiki_db_path, notes_dir=notes)

    assert _derived_texts(wiki_db, eid) == ["Stable body."]  # one claim, not two


def test_editing_body_replaces_the_claim(wiki_db, wiki_db_path, tmp_path):
    eid = _seed_entity(wiki_db, "e_0000000000000001", "Agent Harness")
    notes = tmp_path / "notes"
    _write_note(notes, "note-a1", entities=["Agent Harness"], body="First take.")
    promote_notes(db_path=wiki_db_path, notes_dir=notes)

    _write_note(notes, "note-a1", entities=["Agent Harness"], body="Revised take.")
    promote_notes(db_path=wiki_db_path, notes_dir=notes)

    assert _derived_texts(wiki_db, eid) == ["Revised take."]  # old text gone


def test_note_links_derived_claim_to_all_resolved_entities(wiki_db, wiki_db_path, tmp_path):
    a = _seed_entity(wiki_db, "e_000000000000000a", "Agent Harness")
    b = _seed_entity(wiki_db, "e_000000000000000b", "Progressive Disclosure")
    notes = tmp_path / "notes"
    _write_note(
        notes, "note-multi", entities=["Agent Harness", "Progressive Disclosure"], body="Both."
    )

    promote_notes(db_path=wiki_db_path, notes_dir=notes)

    assert _derived_texts(wiki_db, a) == ["Both."]
    assert _derived_texts(wiki_db, b) == ["Both."]


def test_unpromoting_a_note_removes_its_derived_claim(wiki_db, wiki_db_path, tmp_path):
    eid = _seed_entity(wiki_db, "e_0000000000000001", "Agent Harness")
    notes = tmp_path / "notes"
    _write_note(notes, "note-a1", entities=["Agent Harness"], body="Temporary take.")
    promote_notes(db_path=wiki_db_path, notes_dir=notes)
    assert _derived_texts(wiki_db, eid) == ["Temporary take."]

    # User flips the toggle back off — the derived claim must be reconciled away.
    _write_note(notes, "note-a1", entities=["Agent Harness"], body="Temporary take.", promote=False)
    promote_notes(db_path=wiki_db_path, notes_dir=notes)

    assert _derived_texts(wiki_db, eid) == []


def test_denylisted_hint_is_dropped_not_minted(wiki_db, wiki_db_path, tmp_path):
    # A hint whose name is on the curator denylist (rejected_entities) must not
    # re-mint the tombstoned entity — the resolver doesn't gate the denylist, so
    # promote_notes must filter it before resolution (H1).
    wiki_db.execute(
        "INSERT INTO rejected_entities (normalized_name, rejected_at) VALUES (?, ?)",
        ("banned concept", NOW),
    )
    wiki_db.commit()
    notes = tmp_path / "notes"
    _write_note(notes, "note-bad", entities=["Banned Concept"], body="Should not land.")

    promote_notes(db_path=wiki_db_path, notes_dir=notes)

    from domains.wiki.state import get_all_entities

    assert [e for e in get_all_entities(wiki_db) if e.normalized_name == "banned concept"] == []


def test_result_dirty_flags_only_real_changes(wiki_db, wiki_db_path, tmp_path):
    # The render trigger: a fresh/edited/removed note is dirty (render must run);
    # an unchanged standing note is NOT (re-rendering would churn every page).
    _seed_entity(wiki_db, "e_0000000000000001", "Agent Harness")
    notes = tmp_path / "notes"
    _write_note(notes, "note-a1", entities=["Agent Harness"], body="Take one.")

    assert promote_notes(db_path=wiki_db_path, notes_dir=notes).dirty >= 1  # new
    assert promote_notes(db_path=wiki_db_path, notes_dir=notes).dirty == 0  # unchanged rerun

    _write_note(notes, "note-a1", entities=["Agent Harness"], body="Take one.", promote=False)
    assert promote_notes(db_path=wiki_db_path, notes_dir=notes).dirty >= 1  # removal


def test_backlink_added_to_predating_source_is_dirty(wiki_db, wiki_db_path, tmp_path):
    # A note-source that predates the backlink (url still NULL) must re-render when
    # the backlink lands — else existing notes' pages silently skip the render and
    # the new backlink never surfaces (same body + links, only the url changed).
    _seed_entity(wiki_db, "e_0000000000000001", "Agent Harness")
    notes = tmp_path / "notes"
    _write_note(notes, "note-a1", entities=["Agent Harness"], body="Take one.")
    promote_notes(db_path=wiki_db_path, notes_dir=notes)

    # Simulate the pre-backlink state: wipe the url on the stored source.
    wiki_db.execute("UPDATE sources SET url = NULL WHERE origin_type = 'note'")
    wiki_db.commit()

    assert promote_notes(db_path=wiki_db_path, notes_dir=notes).dirty >= 1


def test_title_only_edit_is_dirty(wiki_db, wiki_db_path, tmp_path):
    # The note caption renders the title, so a title-only edit (same body, hints,
    # date) is render-visible and must be dirty — else the page keeps the old
    # title. A body/url-only check would miss this.
    _seed_entity(wiki_db, "e_0000000000000001", "Agent Harness")
    notes = tmp_path / "notes"
    notes.mkdir()

    def _write(title):
        (notes / "note-a1.md").write_text(
            f"---\ntitle: {title}\ndate: '2026-07-08'\n"
            "entities: [Agent Harness]\npromote: true\n---\n\nSame body.\n"
        )

    _write("Old title")
    promote_notes(db_path=wiki_db_path, notes_dir=notes)
    _write("New title")
    assert promote_notes(db_path=wiki_db_path, notes_dir=notes).dirty >= 1


def test_hint_only_edit_is_dirty(wiki_db, wiki_db_path, tmp_path):
    # Same body, different entity hint: the derived claim relinks A→B, which shifts
    # both entities' page-worthiness counts. dirty must be >=1 so render re-runs —
    # a text_hash-only check would miss this (claim_entities changed, body didn't).
    _seed_entity(wiki_db, "e_000000000000000a", "Agent Harness")
    _seed_entity(wiki_db, "e_000000000000000b", "Progressive Disclosure")
    notes = tmp_path / "notes"
    _write_note(notes, "note-a1", entities=["Agent Harness"], body="Same body.")
    promote_notes(db_path=wiki_db_path, notes_dir=notes)

    _write_note(notes, "note-a1", entities=["Progressive Disclosure"], body="Same body.")
    assert promote_notes(db_path=wiki_db_path, notes_dir=notes).dirty >= 1
