"""Tests for the `wiki-merge` CLI orchestration (domains.wiki.merge_cli).

`run_merge` wraps the pure-DB `merge_entities` transaction with the file-system
side: delete drop's `.md` and re-render keep's frontmatter from the now-unioned
ledgers. Exercised against a real temp wiki.db + temp wiki dir (no mocks — the
whole point is the DB↔file interaction).
"""

from datetime import date

import pytest
from domains.wiki.identity import EntityRecord, normalize_name, slugify
from domains.wiki.io import read_meta, write_page
from domains.wiki.merge_cli import run_merge
from domains.wiki.state import (
    build_entity_index,
    connection,
    get_entity,
    insert_entity,
    insert_page_source,
    upsert_page,
)
from domains.wiki.types import WikiPage

NOW = "2026-06-22T00:00:00+00:00"


def _seed_page(conn, wiki_dir, entity_id, name, file_name, *, item_id):
    """Seed an entity + its page row + a contribution + the on-disk .md."""
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
    insert_page_source(conn, entity_id=entity_id, item_id=item_id, source_type="raw_store")
    write_page(
        wiki_dir / file_name,
        WikiPage(
            entity_id=entity_id,
            title=name,
            page_type="concept",
            summary=f"{name} summary.",
            related=[],
            sources=[item_id],
            updated_at=date(2026, 6, 22),
            content=f"# {name}\n\nbody",
        ),
        aliases=[],
        num_sources=1,
        sources=[item_id],
        related=[],
    )


def test_run_merge_folds_drop_into_keep_and_deletes_its_file(tmp_path, wiki_db_path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    with connection(wiki_db_path) as conn, conn:
        _seed_page(conn, wiki_dir, "e_keep", "Claude Max", "claude-max-d624554e.md", item_id="art1")
        _seed_page(conn, wiki_dir, "e_drop", "Max plan", "max-plan-34db8db7.md", item_id="art2")

    run_merge(wiki_db_path, wiki_dir, keep_id="e_keep", drop_id="e_drop", alias=True)

    assert not (wiki_dir / "max-plan-34db8db7.md").exists()
    assert (wiki_dir / "claude-max-d624554e.md").exists()
    with connection(wiki_db_path) as conn:
        assert get_entity(conn, "e_drop") is None
        assert build_entity_index(conn).by_normalized_alias["max plan"] == "e_keep"


def test_run_merge_rerenders_keep_frontmatter(tmp_path, wiki_db_path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    with connection(wiki_db_path) as conn, conn:
        _seed_page(conn, wiki_dir, "e_keep", "Claude Max", "claude-max-d624554e.md", item_id="art1")
        _seed_page(conn, wiki_dir, "e_drop", "Max plan", "max-plan-34db8db7.md", item_id="art2")

    run_merge(wiki_db_path, wiki_dir, keep_id="e_keep", drop_id="e_drop", alias=True)

    meta = read_meta(wiki_dir / "claude-max-d624554e.md")
    # drop's name folded into keep's alias frontmatter, and both contributions
    # now count toward keep's num_sources.
    assert "Max plan" in meta["aliases"]
    assert meta["num_sources"] == 2


def test_run_merge_dry_run_changes_nothing(tmp_path, wiki_db_path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    with connection(wiki_db_path) as conn, conn:
        _seed_page(conn, wiki_dir, "e_keep", "Claude Max", "claude-max-d624554e.md", item_id="art1")
        _seed_page(conn, wiki_dir, "e_drop", "Max plan", "max-plan-34db8db7.md", item_id="art2")

    result = run_merge(
        wiki_db_path, wiki_dir, keep_id="e_keep", drop_id="e_drop", alias=True, dry_run=True
    )

    assert result.drop_file_path == "max-plan-34db8db7.md"  # the plan is reported
    assert (wiki_dir / "max-plan-34db8db7.md").exists()  # but nothing changed
    with connection(wiki_db_path) as conn:
        assert get_entity(conn, "e_drop") is not None
        assert "max plan" not in build_entity_index(conn).by_normalized_alias


def test_run_merge_rejects_keep_without_a_page(tmp_path, wiki_db_path):
    """Merging into a page-less keep is almost always a wrong-keep-id typo —
    refuse BEFORE the destructive transaction so drop survives intact."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    with connection(wiki_db_path) as conn, conn:
        insert_entity(
            conn,
            EntityRecord(
                entity_id="e_keep",
                canonical_name="Claude Max",
                normalized_name=normalize_name("Claude Max"),
                slug=slugify("Claude Max"),
                page_type="concept",
                created_at=NOW,
            ),
        )  # keep has NO page row
        _seed_page(conn, wiki_dir, "e_drop", "Max plan", "max-plan-34db8db7.md", item_id="art2")

    with pytest.raises(ValueError, match="no page"):
        run_merge(wiki_db_path, wiki_dir, keep_id="e_keep", drop_id="e_drop", alias=True)

    with connection(wiki_db_path) as conn:
        assert get_entity(conn, "e_drop") is not None
    assert (wiki_dir / "max-plan-34db8db7.md").exists()
