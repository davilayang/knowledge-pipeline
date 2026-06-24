"""push_wiki_pages (S3) — wiki.db page-backed entities → Notion 'Wiki Pages'.

Upsert keyed on Entity ID (= wiki.db's surrogate entity_id). Writes ONLY producer
columns (Title / Entity ID / Summary / Source count / Page type / Last updated /
Page status) — never the curator columns. A Notion row whose entity has left
wiki.pages is marked Page status=orphaned (kept, not deleted). Summary is read
from each page's `.md` frontmatter. wiki.db is a real temp SQLite file.
"""

from unittest.mock import MagicMock

import dagster as dg
import pytest
from domains.wiki.identity import EntityRecord, normalize_name, slugify
from domains.wiki.state import (
    connection,
    get_page,
    insert_aliases,
    insert_entity,
    upsert_page,
)
from orchestrators.defs.sync_wiki_curation.assets import (
    PRODUCER_PROPERTIES,
    _build_page_properties,
    push_wiki_pages,
)
from orchestrators.defs.sync_wiki_curation.resources import NotionPageRef
from orchestrators.defs.synthesize_wiki.resources import WikiResource

CURATOR_COLUMNS = {"Rejected", "Reject category", "Reject reason"}


def _entity(entity_id, canonical, page_type="concept") -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id,
        canonical_name=canonical,
        normalized_name=normalize_name(canonical),
        slug=slugify(canonical),
        page_type=page_type,
        created_at="2026-06-23T00:00:00Z",
    )


def _wiki(tmp_path) -> WikiResource:
    return WikiResource(
        backup_dir=str(tmp_path),
        wiki_dir=str(tmp_path / "wiki"),
        wiki_db_path=str(tmp_path / "wiki.db"),
    )


def _seed_page(conn, wiki_dir, entity_id, canonical, *, page_type="concept", summary="S"):
    """Insert a page-backed entity + write its `.md` (frontmatter carries summary)."""
    insert_entity(conn, _entity(entity_id, canonical, page_type))
    file_path = f"{slugify(canonical)}-{entity_id.removeprefix('e_')[:8]}.md"
    upsert_page(conn, entity_id=entity_id, file_path=file_path, related_ids=[])
    path = wiki_dir / file_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nsummary: {summary}\n---\nbody\n", encoding="utf-8")


def _notion(refs: list[NotionPageRef]) -> MagicMock:
    res = MagicMock()
    res.list_pages.return_value = refs
    res.fetch_property_names.return_value = set(PRODUCER_PROPERTIES)
    res.upsert_page.side_effect = lambda **kw: kw.get("page_id") or "new_id"
    return res


def _invoke(wiki, notion):
    ctx = MagicMock(spec=dg.AssetExecutionContext)
    return push_wiki_pages.op.compute_fn.decorated_fn(ctx, wiki=wiki, wiki_pages_notion=notion)


def test_build_page_properties_writes_producer_columns_only():
    props = _build_page_properties(
        entity_id="e_cc",
        title="Claude Code",
        page_type="tool",
        summary="A CLI coding agent.",
        aliases=["claude-code", "cc"],
        source_count=3,
        updated_at="2026-06-22T00:00:00Z",
        page_status="active",
    )

    assert props["Title"]["title"][0]["text"]["content"] == "Claude Code"
    assert props["Entity ID"]["rich_text"][0]["text"]["content"] == "e_cc"
    assert props["Summary"]["rich_text"][0]["text"]["content"] == "A CLI coding agent."
    assert props["Aliases"]["rich_text"][0]["text"]["content"] == "claude-code, cc"
    assert props["Source count"]["number"] == 3
    assert props["Page type"]["select"]["name"] == "tool"
    assert props["Last updated"]["date"]["start"] == "2026-06-22T00:00:00Z"
    assert props["Page status"]["select"]["name"] == "active"
    # Column ownership: the builder NEVER emits a curator column.
    assert CURATOR_COLUMNS.isdisjoint(props)
    assert set(props) == PRODUCER_PROPERTIES


def _active_calls(notion) -> dict:
    """upsert calls that wrote the full producer row, keyed by Entity ID."""
    return {
        c.kwargs["properties"]["Entity ID"]["rich_text"][0]["text"]["content"]: c.kwargs
        for c in notion.upsert_page.call_args_list
        if "Entity ID" in c.kwargs["properties"]
    }


def test_push_creates_new_and_updates_existing(tmp_path):
    wiki = _wiki(tmp_path)
    wiki_dir = wiki.get_wiki_dir()
    with connection(wiki.get_db_path()) as conn, conn:
        _seed_page(conn, wiki_dir, "e_new", "New Thing", summary="A new thing.")
        _seed_page(conn, wiki_dir, "e_exist", "Existing", summary="Existing thing.")

    # Notion already has a row for e_exist (page p1); e_new is absent.
    notion = _notion([NotionPageRef(page_id="p1", entity_id="e_exist", page_status="active")])
    result = _invoke(wiki, notion)

    calls = _active_calls(notion)
    assert calls["e_new"]["page_id"] is None  # create
    assert calls["e_exist"]["page_id"] == "p1"  # update by Entity ID
    # Summary flows from the page's .md frontmatter; status is active.
    assert (
        calls["e_new"]["properties"]["Summary"]["rich_text"][0]["text"]["content"] == "A new thing."
    )
    assert calls["e_new"]["properties"]["Page status"]["select"]["name"] == "active"
    assert result.metadata["created"].value == 1
    assert result.metadata["updated"].value == 1


def test_push_skips_unchanged_active_row(tmp_path):
    """An active row whose stored `Last updated` still equals the page's current
    updated_at hasn't changed since last push — skip it (no Notion write)."""
    wiki = _wiki(tmp_path)
    wiki_dir = wiki.get_wiki_dir()
    with connection(wiki.get_db_path()) as conn, conn:
        _seed_page(conn, wiki_dir, "e_same", "Same")
    with connection(wiki.get_db_path()) as conn:
        updated_at = get_page(conn, "e_same").updated_at

    notion = _notion(
        [
            NotionPageRef(
                page_id="p1", entity_id="e_same", page_status="active", last_updated=updated_at
            )
        ]
    )
    result = _invoke(wiki, notion)

    assert "e_same" not in _active_calls(notion)  # not re-pushed
    assert result.metadata["updated"].value == 0
    assert result.metadata["skipped"].value == 1


def test_push_updates_changed_active_row(tmp_path):
    """An active row whose stored Last updated differs from the page's current
    updated_at has changed since last push — re-push it (not skipped)."""
    wiki = _wiki(tmp_path)
    wiki_dir = wiki.get_wiki_dir()
    with connection(wiki.get_db_path()) as conn, conn:
        _seed_page(conn, wiki_dir, "e_chg", "Changed")

    notion = _notion(
        [
            NotionPageRef(
                page_id="p1",
                entity_id="e_chg",
                page_status="active",
                last_updated="2020-01-01T00:00:00+00:00",
            )
        ]
    )
    result = _invoke(wiki, notion)

    assert _active_calls(notion)["e_chg"]["page_id"] == "p1"  # updated, not skipped
    assert result.metadata["updated"].value == 1
    assert result.metadata["skipped"].value == 0


def test_push_reasserts_orphaned_row_whose_entity_is_active_again(tmp_path):
    """A row currently `orphaned` whose entity is a live page again must be
    re-pushed as active EVEN IF the stored timestamp matches — status is a
    producer field the timestamp alone can't speak for."""
    wiki = _wiki(tmp_path)
    wiki_dir = wiki.get_wiki_dir()
    with connection(wiki.get_db_path()) as conn, conn:
        _seed_page(conn, wiki_dir, "e_back", "Back")
    with connection(wiki.get_db_path()) as conn:
        updated_at = get_page(conn, "e_back").updated_at

    notion = _notion(
        [
            NotionPageRef(
                page_id="p1", entity_id="e_back", page_status="orphaned", last_updated=updated_at
            )
        ]
    )
    result = _invoke(wiki, notion)

    call = _active_calls(notion)["e_back"]
    assert call["page_id"] == "p1"
    assert call["properties"]["Page status"]["select"]["name"] == "active"
    assert result.metadata["skipped"].value == 0


def test_push_writes_alias_family_from_table(tmp_path):
    """Aliases column = the authoritative alias table for the entity (comma-
    joined). A homonym suppressed at merge time (wiki-merge --no-alias) is simply
    absent from the table, so it never reaches the column — the push is read-only
    over aliases and never affects resolution."""
    wiki = _wiki(tmp_path)
    wiki_dir = wiki.get_wiki_dir()
    with connection(wiki.get_db_path()) as conn, conn:
        _seed_page(conn, wiki_dir, "e_max", "Claude Max")
        insert_aliases(conn, [("Max plan", "e_max")])  # an aliased surface form

    notion = _notion([])
    _invoke(wiki, notion)

    (call,) = notion.upsert_page.call_args_list
    assert call.kwargs["properties"]["Aliases"]["rich_text"][0]["text"]["content"] == "Max plan"


def test_push_orphans_rows_whose_entity_left_wiki(tmp_path):
    """A Notion row whose entity is no longer a live page (rejected/merged/removed)
    is marked orphaned — kept, not deleted — and an already-orphaned row is left
    untouched (no redundant write)."""
    wiki = _wiki(tmp_path)
    wiki_dir = wiki.get_wiki_dir()
    with connection(wiki.get_db_path()) as conn, conn:
        _seed_page(conn, wiki_dir, "e_live", "Live")

    notion = _notion(
        [
            NotionPageRef(page_id="p_live", entity_id="e_live", page_status="active"),
            NotionPageRef(page_id="p_gone", entity_id="e_gone", page_status="active"),
            NotionPageRef(page_id="p_old", entity_id="e_old", page_status="orphaned"),
        ]
    )
    result = _invoke(wiki, notion)

    orphan_calls = [
        c
        for c in notion.upsert_page.call_args_list
        if set(c.kwargs["properties"]) == {"Page status"}
    ]
    assert len(orphan_calls) == 1
    assert orphan_calls[0].kwargs["page_id"] == "p_gone"
    assert orphan_calls[0].kwargs["properties"]["Page status"]["select"]["name"] == "orphaned"
    assert result.metadata["updated"].value == 1  # e_live
    assert result.metadata["orphaned"].value == 1  # e_gone (e_old already orphaned)


def test_push_fails_loud_on_schema_drift(tmp_path):
    """A renamed/removed producer column must abort the run BEFORE any write —
    writing into a half-present schema would silently lose columns."""
    wiki = _wiki(tmp_path)
    wiki_dir = wiki.get_wiki_dir()
    with connection(wiki.get_db_path()) as conn, conn:
        _seed_page(conn, wiki_dir, "e_x", "Thing")

    notion = _notion([])
    notion.fetch_property_names.return_value = set(PRODUCER_PROPERTIES) - {"Source count"}

    with pytest.raises(dg.Failure, match="Source count"):
        _invoke(wiki, notion)

    notion.upsert_page.assert_not_called()  # failed before any write
