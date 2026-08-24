"""Tests for build_wiki_index — the whole-wiki `_index/resolve.json` sidecar +
`index.md` TOC (the producer side of the newsletter-assistant wiki bridge).

Seeds a real wiki.db + wiki_dir via synthesize_source + render_entity_pages
(same pattern as test_attributed_synthesis), then builds the index over it.
"""

import json

import pytest
from domains.wiki.state import connection, get_all_entities, insert_aliases
from workflows.wiki_synthesis.attributed_synthesis import (
    render_entity_pages,
    synthesize_source,
)
from workflows.wiki_synthesis.wiki_index import build_wiki_index

NOW = "2026-07-02T00:00:00+00:00"

# Two page-worthy entities (GraphRAG, Microsoft) co-mentioned in one source.
_CLAIMS_DOC = (
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
_CANDIDATES_DOC = "GraphRAG — concept\nMicrosoft — organization\n"


def _source():
    return {
        "url": "https://medium.com/x",
        "canonical_url": "https://medium.com/x",
        "title": "T",
        "author": "Jane Doe",
        "content_date": "2026-03-01",
        "content_hash": "h",
    }


def _seed(wiki_db_path):
    from workflows.wiki_synthesis.attributed_synthesis import build_source_record

    synthesize_source(
        claims_doc=_CLAIMS_DOC,
        candidates_doc=_CANDIDATES_DOC,
        source=build_source_record(_source(), now=NOW),
        wiki_db_path=wiki_db_path,
    )


def _read_resolve(wiki_dir):
    return json.loads((wiki_dir / "_index" / "resolve.json").read_text(encoding="utf-8"))


def test_resolve_aliases_map_self_map_canonical_and_alias(tmp_path, wiki_db_path):
    # aliases map = every entity_id self-mapped + every canonical name + every
    # alias, all lowercased → owning entity_id. The self-map is contract-critical:
    # an entity with no alias rows must still resolve by its own id.
    _seed(wiki_db_path)
    wiki_dir = tmp_path / "wiki"
    render_entity_pages(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)

    with connection(wiki_db_path) as conn:
        by_name = {e.canonical_name: e for e in get_all_entities(conn)}
        gr, ms = by_name["GraphRAG"], by_name["Microsoft"]
        with conn:
            insert_aliases(conn, [("GRAG", gr.entity_id)])

    build_wiki_index(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)

    aliases = _read_resolve(wiki_dir)["aliases"]
    assert aliases[gr.entity_id] == gr.entity_id  # self-map
    assert aliases[ms.entity_id] == ms.entity_id  # self-map (no alias rows)
    assert aliases["graphrag"] == gr.entity_id  # canonical, lowercased
    assert aliases["microsoft"] == ms.entity_id
    assert aliases["grag"] == gr.entity_id  # alias, lowercased


def test_resolve_entities_map_orientation_fields(tmp_path, wiki_db_path):
    # entities map keys on entity_id → {name, type, file, num_sources}; `file`
    # matches the on-disk .md the reader will open.
    _seed(wiki_db_path)
    wiki_dir = tmp_path / "wiki"
    render_entity_pages(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)
    with connection(wiki_db_path) as conn:
        gr = {e.canonical_name: e for e in get_all_entities(conn)}["GraphRAG"]

    build_wiki_index(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)

    ent = _read_resolve(wiki_dir)["entities"][gr.entity_id]
    assert ent["name"] == "GraphRAG"
    assert ent["type"] == "concept"
    assert ent["num_sources"] == 1
    assert (wiki_dir / ent["file"]).exists()


def _add_derived_claim(wiki_db_path, entity_id, *, note_title, body):
    from domains.wiki.attributed import (
        ClaimRecord,
        SourceRecord,
        claim_text_hash,
        insert_claim,
        insert_claim_entity,
        mint_claim_id,
        mint_source_id,
        upsert_source,
    )

    ck = f"local:{note_title}"
    sid = mint_source_id(ck)
    th = claim_text_hash(body)
    with connection(wiki_db_path) as conn, conn:
        upsert_source(
            conn,
            SourceRecord(
                sid, ck, "note", note_title, None, None, None, "2026-07-08", None, None, NOW
            ),
        )
        cid = insert_claim(
            conn, ClaimRecord(mint_claim_id(sid, th), sid, body, th, "user", None, NOW)
        )
        insert_claim_entity(conn, claim_id=cid, entity_id=entity_id)


def test_resolve_entities_flag_has_derived(tmp_path, wiki_db_path):
    # A page carrying a promoted note (a `derived` claim) advertises has_derived:
    # true in resolve.json so NA / MCP can flag pages that hold the user's own
    # synthesis; a source-only page is has_derived: false.
    _seed(wiki_db_path)
    wiki_dir = tmp_path / "wiki"
    render_entity_pages(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)
    with connection(wiki_db_path) as conn:
        by_name = {e.canonical_name: e for e in get_all_entities(conn)}
        gr, ms = by_name["GraphRAG"], by_name["Microsoft"]
    _add_derived_claim(wiki_db_path, gr.entity_id, note_title="My note", body="A harness body.")

    build_wiki_index(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)

    entities = _read_resolve(wiki_dir)["entities"]
    assert entities[gr.entity_id]["has_derived"] is True
    assert entities[ms.entity_id]["has_derived"] is False


def test_alias_collision_raises(tmp_path, wiki_db_path):
    # One lowercased key owned by two different entity_ids is a contract error:
    # alias "Microsoft" on GraphRAG's id collides with Microsoft's canonical name.
    _seed(wiki_db_path)
    wiki_dir = tmp_path / "wiki"
    render_entity_pages(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)
    with connection(wiki_db_path) as conn:
        gr = {e.canonical_name: e for e in get_all_entities(conn)}["GraphRAG"]
        with conn:
            insert_aliases(conn, [("Microsoft", gr.entity_id)])

    with pytest.raises(ValueError, match="collision"):
        build_wiki_index(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)


def test_snapshot_id_and_page_hash_stable_then_change(tmp_path, wiki_db_path):
    # page_hash hashes the on-disk .md (so NA detects a torn read); snapshot_id
    # hashes the whole page set. Both stable across an unchanged rebuild; editing
    # one page changes its page_hash and the snapshot_id.
    _seed(wiki_db_path)
    wiki_dir = tmp_path / "wiki"
    render_entity_pages(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)
    with connection(wiki_db_path) as conn:
        gr = {e.canonical_name: e for e in get_all_entities(conn)}["GraphRAG"]

    build_wiki_index(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)
    r1 = _read_resolve(wiki_dir)
    build_wiki_index(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)
    r2 = _read_resolve(wiki_dir)

    assert r1["snapshot_id"] == r2["snapshot_id"]
    assert r1["entities"][gr.entity_id]["page_hash"] == r2["entities"][gr.entity_id]["page_hash"]

    gr_file = wiki_dir / r1["entities"][gr.entity_id]["file"]
    gr_file.write_text(gr_file.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")
    build_wiki_index(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)
    r3 = _read_resolve(wiki_dir)

    assert r3["entities"][gr.entity_id]["page_hash"] != r1["entities"][gr.entity_id]["page_hash"]
    assert r3["snapshot_id"] != r1["snapshot_id"]


def test_index_md_groups_by_live_entity_type(tmp_path, wiki_db_path):
    # TOC groups by whatever entity_type values are live (here concept +
    # organization — organization proves the old concept|tool|trend hardcode is
    # gone), links labelled with the canonical name → on-disk file.
    _seed(wiki_db_path)
    wiki_dir = tmp_path / "wiki"
    render_entity_pages(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)
    with connection(wiki_db_path) as conn:
        ents = {e.canonical_name: e for e in get_all_entities(conn)}

    build_wiki_index(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)
    md = (wiki_dir / "index.md").read_text(encoding="utf-8")

    resolve = _read_resolve(wiki_dir)["entities"]
    gr_file = resolve[ents["GraphRAG"].entity_id]["file"]
    assert "## Concept" in md
    assert "## Organization" in md
    assert f"- [GraphRAG]({gr_file})" in md


def test_alias_only_change_rewrites_resolve(tmp_path, wiki_db_path):
    # An alias added without any page re-render must still reach resolve.json:
    # snapshot_id fingerprints the whole payload (not just page bytes), so the
    # skip doesn't strand a new alias with stale on-disk resolution.
    _seed(wiki_db_path)
    wiki_dir = tmp_path / "wiki"
    render_entity_pages(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)

    build_wiki_index(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)
    r1 = _read_resolve(wiki_dir)

    with connection(wiki_db_path) as conn:
        gr = {e.canonical_name: e for e in get_all_entities(conn)}["GraphRAG"]
        with conn:
            insert_aliases(conn, [("GRAG", gr.entity_id)])

    result = build_wiki_index(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)
    r2 = _read_resolve(wiki_dir)

    assert result.resolve_written
    assert r2["snapshot_id"] != r1["snapshot_id"]
    assert r2["aliases"]["grag"] == gr.entity_id


def test_skip_when_unchanged_and_self_heal(tmp_path, wiki_db_path):
    # First build writes both files; an unchanged rebuild writes neither (no
    # mtime churn). Deleting a file self-heals on the next build even though the
    # DB didn't change.
    _seed(wiki_db_path)
    wiki_dir = tmp_path / "wiki"
    render_entity_pages(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)

    r1 = build_wiki_index(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)
    assert r1.resolve_written and r1.index_written

    r2 = build_wiki_index(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)
    assert not r2.resolve_written and not r2.index_written

    (wiki_dir / "index.md").unlink()
    r3 = build_wiki_index(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)
    assert r3.index_written and not r3.resolve_written
    assert (wiki_dir / "index.md").exists()


def test_resolve_entities_carry_has_user_claims(tmp_path, wiki_db_path):
    # `has_user_claims` is the name that says what the flag MEANS — the consumer
    # already speaks it as "Includes your own notes on this." The old
    # `has_derived` name described how it used to be computed, back when a
    # promoted note was stored under the value meaning "the pipeline made this".
    _seed(wiki_db_path)
    wiki_dir = tmp_path / "wiki"
    render_entity_pages(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)
    with connection(wiki_db_path) as conn:
        by_name = {e.canonical_name: e for e in get_all_entities(conn)}
        gr, ms = by_name["GraphRAG"], by_name["Microsoft"]
    _add_derived_claim(wiki_db_path, gr.entity_id, note_title="My note", body="A harness body.")

    build_wiki_index(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)

    entities = _read_resolve(wiki_dir)["entities"]
    assert entities[gr.entity_id]["has_user_claims"] is True
    assert entities[ms.entity_id]["has_user_claims"] is False


def test_both_sidecar_key_spellings_always_agree(tmp_path, wiki_db_path):
    # `has_derived` is written alongside `has_user_claims` purely so a consumer
    # rollback still finds the key — they are the SAME fact under two names, and
    # the release only stays additive while they agree. The sibling tests assert
    # each spelling separately, which would not catch the two drifting apart.
    _seed(wiki_db_path)
    wiki_dir = tmp_path / "wiki"
    render_entity_pages(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)
    with connection(wiki_db_path) as conn:
        by_name = {e.canonical_name: e for e in get_all_entities(conn)}
        gr = by_name["GraphRAG"]
    _add_derived_claim(wiki_db_path, gr.entity_id, note_title="My note", body="A harness body.")

    build_wiki_index(wiki_db_path=wiki_db_path, wiki_dir=wiki_dir)

    entities = _read_resolve(wiki_dir)["entities"]
    # covers both a true and a false entity — GraphRAG carries the note, the rest do not
    assert {e["has_user_claims"] for e in entities.values()} == {True, False}
    for eid, e in entities.items():
        assert e["has_user_claims"] == e["has_derived"], f"{eid} disagrees across spellings"
