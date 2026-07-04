"""Tests for the `wiki-merge` CLI orchestration (domains.wiki.merge_cli).

`run_merge` wraps the pure-DB `merge_entities` transaction with the file-system
side: unlink drop's `.md`, and (opt-in) snapshot wiki.db first. Re-rendering the
survivor is a SEPARATE post-batch `render_pages` sweep, not this CLI's job.
Exercised against a real temp wiki.db + temp wiki dir.
"""

from datetime import date

from domains.wiki.identity import EntityRecord, normalize_name, slugify
from domains.wiki.io import write_page
from domains.wiki.merge_cli import run_merge
from domains.wiki.state import (
    connection,
    get_aliases_for_entity,
    get_entity,
    insert_entity,
    upsert_page,
)
from domains.wiki.types import WikiPage

NOW = "2026-07-04T00:00:00+00:00"


def _seed_page(conn, wiki_dir, entity_id, name, file_name):
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
    upsert_page(conn, entity_id=entity_id, file_path=file_name, related_ids=[])
    write_page(
        wiki_dir / file_name,
        WikiPage(
            entity_id=entity_id,
            title=name,
            entity_type="concept",
            summary=f"{name} summary.",
            related=[],
            sources=["art1"],
            updated_at=date(2026, 7, 4),
            content=f"# {name}\n\nbody",
        ),
        aliases=[],
        num_sources=1,
        sources=["art1"],
        related=[],
    )


def _seed_keep_and_drop(wiki_dir, wiki_db_path):
    wiki_dir.mkdir()
    with connection(wiki_db_path) as conn, conn:
        _seed_page(conn, wiki_dir, "e_keep", "AI agents", "ai_agents-keep.md")
        _seed_page(conn, wiki_dir, "e_drop", "AI agent", "ai_agent-drop.md")


def test_run_merge_deletes_drop_and_unlinks_its_file(tmp_path, wiki_db_path):
    wiki_dir = tmp_path / "wiki"
    _seed_keep_and_drop(wiki_dir, wiki_db_path)

    run_merge(wiki_db_path, wiki_dir, keep_id="e_keep", drop_id="e_drop")

    assert not (wiki_dir / "ai_agent-drop.md").exists()  # drop's page unlinked
    assert (wiki_dir / "ai_agents-keep.md").exists()  # keep's page stays
    with connection(wiki_db_path) as conn:
        assert get_entity(conn, "e_drop") is None
        assert get_entity(conn, "e_keep") is not None
        assert "AI agent" in get_aliases_for_entity(conn, "e_keep")  # name aliased


def test_run_merge_dry_run_changes_nothing(tmp_path, wiki_db_path):
    wiki_dir = tmp_path / "wiki"
    _seed_keep_and_drop(wiki_dir, wiki_db_path)

    run_merge(wiki_db_path, wiki_dir, keep_id="e_keep", drop_id="e_drop", dry_run=True)

    assert (wiki_dir / "ai_agent-drop.md").exists()
    with connection(wiki_db_path) as conn:
        assert get_entity(conn, "e_drop") is not None
        assert get_aliases_for_entity(conn, "e_keep") == []


def test_run_merge_no_alias_skips_the_name(tmp_path, wiki_db_path):
    wiki_dir = tmp_path / "wiki"
    _seed_keep_and_drop(wiki_dir, wiki_db_path)

    run_merge(wiki_db_path, wiki_dir, keep_id="e_keep", drop_id="e_drop", no_alias=True)

    with connection(wiki_db_path) as conn:
        assert get_entity(conn, "e_drop") is None  # still merged
        assert get_aliases_for_entity(conn, "e_keep") == []  # but name not aliased


def test_run_merge_backup_snapshots_the_db(tmp_path, wiki_db_path):
    wiki_dir = tmp_path / "wiki"
    _seed_keep_and_drop(wiki_dir, wiki_db_path)

    run_merge(wiki_db_path, wiki_dir, keep_id="e_keep", drop_id="e_drop", backup=True)

    snaps = list(wiki_db_path.parent.glob(f"{wiki_db_path.name}.pre-merge-*"))
    assert len(snaps) == 1  # a point-in-time copy was written before mutating
    # the snapshot holds the PRE-merge state (drop still present) even though the
    # live db has folded it — proves the backup captured committed data (WAL too).
    with connection(snaps[0]) as snap:
        assert get_entity(snap, "e_drop") is not None
    with connection(wiki_db_path) as conn:
        assert get_entity(conn, "e_drop") is None
