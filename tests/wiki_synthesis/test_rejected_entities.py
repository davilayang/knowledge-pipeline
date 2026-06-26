"""W2.5 Part B — deterministic entity rejection list (denylist).

The denylist keys on NORMALISED names (the surrogate id is minted after
extraction, so it can't be keyed on id). A candidate is dropped if its extracted
name — or its resolved entity's canonical name — normalises into the set, so
synthesis never builds or updates a page for it. Tests drive the public
interface (synthesize_item) with the LLM calls mocked and a real wiki.db.
"""

from pathlib import Path
from unittest.mock import patch

from domains.wiki.state import (
    connect,
    get_all_entities,
    get_processed_ids,
)
from domains.wiki.types import ExtractedEntity, ExtractionResult
from workflows.wiki_synthesis.synthesize import synthesize_item

from tests.wiki_synthesis._helpers import build_synthesis_output, make_item, make_llm_call


def _canonical_names(db_path: Path) -> set[str]:
    conn = connect(db_path)
    try:
        return {e.canonical_name for e in get_all_entities(conn)}
    finally:
        conn.close()


def _rag_and_cli() -> ExtractionResult:
    return ExtractionResult(
        entities=[
            ExtractedEntity(title="RAG", page_type="concept"),
            ExtractedEntity(title="CLI", page_type="tool", aliases=["command line interface"]),
        ]
    )


def test_denylisted_entity_gets_no_page(tmp_path: Path, wiki_db_path):
    """A normalised name on the rejection list is never built; a sibling is."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    with (
        patch(
            "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
            return_value=(_rag_and_cli(), make_llm_call(model="gpt-4.1-nano")),
        ),
        patch(
            "workflows.wiki_synthesis.synthesize.generate_with_usage",
            return_value=make_llm_call(content=build_synthesis_output("RAG")),
        ),
    ):
        synthesize_item(
            # Both salient (in title) so CLI's absence is the denylist's doing, not
            # the salience gate's — otherwise a peripheral CLI would vanish anyway.
            make_item(title="RAG and CLI"),
            db_path=wiki_db_path,
            wiki_dir=wiki_dir,
            rejected_entities=frozenset({"cli"}),
        )

    assert _canonical_names(wiki_db_path) == {"RAG"}
    conn = connect(wiki_db_path)
    try:
        assert get_processed_ids(conn, status="ok") == {"content_abc"}
    finally:
        conn.close()


def test_all_denylisted_commits_skipped(tmp_path: Path, wiki_db_path):
    """Every extracted entity rejected → 'skipped', not 'error'."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = ExtractionResult(
        entities=[
            ExtractedEntity(title="CLI", page_type="tool"),
            ExtractedEntity(title="API", page_type="tool"),
        ]
    )

    with patch(
        "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
        return_value=(extraction, make_llm_call(model="gpt-4.1-nano")),
    ):
        synthesize_item(
            # Both salient (in title) so they'd synthesize if not rejected — the
            # empty-md assertion below then proves the denylist, not the gate.
            make_item(title="CLI and API"),
            db_path=wiki_db_path,
            wiki_dir=wiki_dir,
            rejected_entities=frozenset({"cli", "api"}),
        )

    assert list(wiki_dir.glob("*.md")) == []
    conn = connect(wiki_db_path)
    try:
        assert get_processed_ids(conn, status="skipped") == {"content_abc"}
        assert get_processed_ids(conn, status="error") == set()
        assert get_processed_ids(conn, status="ok") == set()
    finally:
        conn.close()


def test_denylisted_new_entity_leaves_no_alias(tmp_path: Path, wiki_db_path):
    """A rejected new entity must not persist its entity row or its aliases."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    with (
        patch(
            "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
            return_value=(_rag_and_cli(), make_llm_call(model="gpt-4.1-nano")),
        ),
        patch(
            "workflows.wiki_synthesis.synthesize.generate_with_usage",
            return_value=make_llm_call(content=build_synthesis_output("RAG")),
        ),
    ):
        synthesize_item(
            # CLI salient (in title) so its alias would persist if not rejected —
            # the alias-count assertion then proves the denylist, not the gate.
            make_item(title="RAG and CLI"),
            db_path=wiki_db_path,
            wiki_dir=wiki_dir,
            rejected_entities=frozenset({"cli"}),
        )

    assert _canonical_names(wiki_db_path) == {"RAG"}
    conn = connect(wiki_db_path)
    try:
        rows = conn.execute(
            "SELECT count(*) FROM aliases WHERE normalized_alias = 'command line interface'"
        ).fetchone()
    finally:
        conn.close()
    assert rows[0] == 0
