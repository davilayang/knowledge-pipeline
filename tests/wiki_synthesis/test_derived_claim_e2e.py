"""End-to-end guard for the derived-claim render path (KP-1 schema + KP-3 render).

Drives the REAL pipeline on a fresh on-disk wiki.db: create_schema (the actual
schema file, not the fixture) → write a note-as-source + a `derived` claim +
a `reported` claim → render_entity_pages writes a real .md file. Proves the
schema admits a note-origin source and a derived claim, and that render surfaces
the derived claim under its own `## From my notes` section (the user's synthesis)
without leaking it into the source-side Reported/Opinion sections.
"""

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
from domains.wiki.identity import EntityRecord, normalize_name, slugify
from domains.wiki.state import connection, create_schema, insert_entity
from workflows.wiki_synthesis.attributed_synthesis import render_entity_pages

NOW = "2026-07-09T00:00:00+00:00"


def _source(content_key, origin_type):
    return SourceRecord(
        source_id=mint_source_id(content_key),
        content_key=content_key,
        origin_type=origin_type,
        title="A Title",
        author=None,
        publication="Codrift",
        url="https://ex.com/a",
        published_at="2026-03-01",
        content_hash="h",
        fetched_at=NOW,
        added_at=NOW,
    )


def _claim(source_id, text, kind):
    th = claim_text_hash(text)
    return ClaimRecord(
        claim_id=mint_claim_id(source_id, th),
        source_id=source_id,
        text=text,
        text_hash=th,
        claim_kind=kind,
        created_at=NOW,
    )


def test_derived_claim_flows_through_render_without_leaking(tmp_path):
    db_path = tmp_path / "wiki.db"
    wiki_dir = tmp_path / "wiki"
    create_schema(db_path=db_path)

    entity = EntityRecord(
        entity_id="e_0000000000000001",
        canonical_name="Agent Harness",
        normalized_name=normalize_name("Agent Harness"),
        slug=slugify("Agent Harness"),
        entity_type="concept",
        created_at=NOW,
    )
    reported_text = "The harness gained subagents."
    derived_text = "My take: the harness is a progressive-disclosure engine."

    with connection(db_path) as conn:
        with conn:
            insert_entity(conn, entity)
            # A normal article source with a reported claim (earns page-worthiness).
            article = _source("medium::https://ex.com/a", "queue")
            upsert_source(conn, article)
            rc = _claim(article.source_id, reported_text, "reported")
            insert_claim(conn, rc)
            insert_claim_entity(conn, claim_id=rc.claim_id, entity_id=entity.entity_id)
            # A promoted note, stored as a note-origin source with a derived claim.
            note = _source("local:2026-07-08_agent-harness-a1b2c3", "note")
            upsert_source(conn, note)
            dc = _claim(note.source_id, derived_text, "derived")
            insert_claim(conn, dc)
            insert_claim_entity(conn, claim_id=dc.claim_id, entity_id=entity.entity_id)

    written = render_entity_pages(wiki_db_path=db_path, wiki_dir=wiki_dir)

    assert written == [entity.entity_id]
    pages = list(wiki_dir.glob("*.md"))
    assert len(pages) == 1
    page = pages[0].read_text()
    assert reported_text in page  # source-side claim renders
    assert "## From my notes" in page
    assert derived_text in page  # the user's synthesis renders under From my notes
    # No leak: the derived claim must NOT appear in the source-side Reported section.
    assert derived_text not in page.split("## Reported", 1)[1]
