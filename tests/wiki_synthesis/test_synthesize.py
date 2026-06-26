"""End-to-end tests for synthesize_item — the plain-function workflow entry.

Drives the public interface (synthesize_item) with the two LLM calls mocked at
the workflows.wiki_synthesis.synthesize boundary and a real wiki.db (SQLite).
Covers the happy path, the denylist thread-through, re-run semantics, and the
Langfuse trace-attribute wiring (configured vs unconfigured).

Surrogate ids are minted per run and filenames are flat `{slug}-{shortid}.md`,
so assertions key on canonical names / file counts, never on a fixed id.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from domains.wiki.identity import Candidate
from domains.wiki.state import connect, get_all_entities, get_all_pages, get_processed_ids
from domains.wiki.types import ExtractedEntity, ExtractionResult
from workflows.wiki_synthesis.synthesize import extract, synthesize_from_candidates, synthesize_item

from tests.wiki_synthesis._helpers import build_synthesis_output, make_item, make_llm_call


def _runner_item():
    return make_item(
        item_id="content_runner",
        title="Runner Test Article",
        text="# Test\n\nA test article.",
        source_ref="raw_store:content_runner",
    )


def _processed_ids(db_path: Path, status: str) -> set[str]:
    conn = connect(db_path)
    try:
        return get_processed_ids(conn, status=status)
    finally:
        conn.close()


def _canonical_names(db_path: Path) -> set[str]:
    conn = connect(db_path)
    try:
        return {e.canonical_name for e in get_all_entities(conn)}
    finally:
        conn.close()


def _page_count(db_path: Path) -> int:
    conn = connect(db_path)
    try:
        return len(get_all_pages(conn))
    finally:
        conn.close()


def test_synthesize_item_end_to_end(tmp_path: Path, wiki_db_path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = ExtractionResult(entities=[ExtractedEntity(title="Test", page_type="concept")])

    with (
        patch(
            "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
            return_value=(extraction, make_llm_call(model="gpt-4.1-nano")),
        ),
        patch(
            "workflows.wiki_synthesis.synthesize.generate_with_usage",
            return_value=make_llm_call(content=build_synthesis_output("Test")),
        ),
    ):
        synthesize_item(_runner_item(), db_path=wiki_db_path, wiki_dir=wiki_dir)

    md_files = list(wiki_dir.glob("*.md"))
    assert len(md_files) == 1
    assert _processed_ids(wiki_db_path, "ok") == {"content_runner"}
    assert _canonical_names(wiki_db_path) == {"Test"}
    assert _page_count(wiki_db_path) == 1


def test_synthesize_from_candidates_skips_extraction(tmp_path: Path, wiki_db_path):
    """synthesize_from_candidates resolves + synthesizes pre-extracted candidates
    WITHOUT calling the extraction LLM — the decoupling the wiki/extracted asset
    relies on (extraction runs in its own stage; synthesis consumes its output)."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    candidates = [Candidate(name="Test", page_type="concept")]

    with (
        patch("workflows.wiki_synthesis.synthesize.generate_structured_with_usage") as mock_extract,
        patch(
            "workflows.wiki_synthesis.synthesize.generate_with_usage",
            return_value=make_llm_call(content=build_synthesis_output("Test")),
        ),
    ):
        res = synthesize_from_candidates(
            _runner_item(), candidates, db_path=wiki_db_path, wiki_dir=wiki_dir
        )

    mock_extract.assert_not_called()  # extraction LLM never touched
    assert res["status"] == "ok"
    assert _canonical_names(wiki_db_path) == {"Test"}
    assert len(list(wiki_dir.glob("*.md"))) == 1


def test_two_candidates_one_entity_synthesizes_once(tmp_path: Path, wiki_db_path):
    """Two candidates in one item that normalise to the same entity collapse to
    a single synthesis call + one page — not duplicate LLM spend."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = ExtractionResult(
        entities=[
            ExtractedEntity(title="Model Costs", page_type="concept"),
            ExtractedEntity(title="model costs", page_type="trend"),  # same normalised name
        ]
    )
    # Entity in the title so it clears the salience gate and synthesizes.
    item = make_item(item_id="content_runner", title="Model Costs", text="On model costs.")
    synthesis_calls = 0

    def counting_generate(prompt, *, system="", model=""):
        nonlocal synthesis_calls
        synthesis_calls += 1
        return make_llm_call(content=build_synthesis_output("Model Costs"))

    with (
        patch(
            "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
            return_value=(extraction, make_llm_call(model="gpt-4.1-nano")),
        ),
        patch(
            "workflows.wiki_synthesis.synthesize.generate_with_usage",
            side_effect=counting_generate,
        ),
    ):
        synthesize_item(item, db_path=wiki_db_path, wiki_dir=wiki_dir)

    assert synthesis_calls == 1  # synthesized once, not once per candidate
    assert _page_count(wiki_db_path) == 1
    assert len(list(wiki_dir.glob("*.md"))) == 1


def test_synthesize_extracted_item_marks_extraction_error(tmp_path: Path, wiki_db_path):
    """An item whose extraction failed is recorded processed='error' (not stuck)
    and runs no synthesis LLM call."""
    from workflows.wiki_synthesis.synthesize import synthesize_extracted_item

    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    item = _runner_item()

    with patch("workflows.wiki_synthesis.synthesize.generate_with_usage") as mock_synth:
        res = synthesize_extracted_item(
            item,
            {"candidates": [], "extract_error": "RuntimeError: extraction blew up"},
            db_path=wiki_db_path,
            wiki_dir=wiki_dir,
        )

    mock_synth.assert_not_called()
    assert res["status"] == "error"
    assert _processed_ids(wiki_db_path, "error") == {item.item_id}


def test_extract_item_traces_under_distinct_extract_span(tmp_path: Path, wiki_db_path):
    """extract_item opens a span named wiki_extract__<id> — distinct from the
    synthesis span — so the two stages are tellable apart in Langfuse while
    sharing the same session_id."""
    from workflows.wiki_synthesis.synthesize import extract_item

    fake_client = MagicMock()
    with (
        patch.dict(os.environ, {"LANGFUSE_PUBLIC_KEY": "test_pk"}),
        patch(
            "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
            return_value=(ExtractionResult(entities=[]), make_llm_call(model="gpt-4.1-nano")),
        ),
        patch("langfuse.get_client", return_value=fake_client),
    ):
        extract_item(_runner_item(), db_path=wiki_db_path)

    fake_client.start_as_current_span.assert_called_once_with(name="wiki_extract__content_runner")
    assert fake_client.update_current_trace.call_args.kwargs["session_id"] == "content_runner"


def test_extract_relevance_filters_large_catalog_in_prompt(wiki_db_path):
    """With a catalog past the relevance cap, extract() sends only the entities
    lexically relevant to the article into the prompt — an off-topic entity is
    dropped, the on-topic one survives. Below the cap nothing changes (covered in
    domains.wiki.relevance tests)."""
    from domains.wiki.identity import EntityRecord, normalize_name, slugify
    from domains.wiki.relevance import RELEVANCE_MAX_ENTITIES
    from domains.wiki.state import connection, insert_entity

    def _rec(name: str) -> EntityRecord:
        return EntityRecord(
            entity_id="e_" + slugify(name),
            canonical_name=name,
            normalized_name=normalize_name(name),
            slug=slugify(name),
            page_type="concept",
            created_at="2026-06-22",
        )

    names = [f"Filler Topic {i}" for i in range(RELEVANCE_MAX_ENTITIES)] + ["Knowledge Graph"]
    with connection(wiki_db_path) as conn, conn:
        for name in names:
            insert_entity(conn, _rec(name))

    item = make_item(item_id="kg1", title="On graphs", text="A piece on the knowledge graph.")
    with patch(
        "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
        return_value=(ExtractionResult(entities=[]), make_llm_call(model="gpt-4.1-nano")),
    ) as mock_extract:
        extract(item, db_path=wiki_db_path)

    user_prompt = mock_extract.call_args.args[0]
    assert "Knowledge Graph" in user_prompt  # relevant entity kept
    assert "Filler Topic 0" not in user_prompt  # off-topic entity trimmed


def test_synthesize_item_honours_rejected_entities(tmp_path: Path, wiki_db_path):
    """A denylisted entity is never built; its sibling still is (W2.5 seam)."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = ExtractionResult(
        entities=[
            ExtractedEntity(title="Test", page_type="concept"),
            ExtractedEntity(title="CLI", page_type="tool"),
        ]
    )

    with (
        patch(
            "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
            return_value=(extraction, make_llm_call(model="gpt-4.1-nano")),
        ),
        patch(
            "workflows.wiki_synthesis.synthesize.generate_with_usage",
            return_value=make_llm_call(content=build_synthesis_output("Test")),
        ),
    ):
        synthesize_item(
            # Both salient (in title) so CLI's absence is the denylist, not the
            # salience gate — a peripheral CLI would never get a page anyway.
            make_item(item_id="content_runner", title="Test and CLI"),
            db_path=wiki_db_path,
            wiki_dir=wiki_dir,
            rejected_entities=frozenset({"cli"}),
        )

    assert _canonical_names(wiki_db_path) == {"Test"}
    assert _page_count(wiki_db_path) == 1


def test_synthesize_item_re_runs_on_completed_item(tmp_path: Path, wiki_db_path):
    """Calling synthesize_item twice for the same item runs synthesis twice —
    not a no-op (no checkpointer to resume from). The second run reuses the
    entity (exact normalised-name match → same surrogate → page UPDATE, not a
    duplicate), and the processed row is upserted, not duplicated."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = ExtractionResult(entities=[ExtractedEntity(title="Rerun", page_type="concept")])
    # Entity in the title so it clears the salience gate on both runs.
    item = make_item(item_id="content_rerun", title="Rerun", source_ref="raw_store:content_rerun")
    synthesis_calls = 0

    def counting_generate(prompt, *, system="", model=""):
        nonlocal synthesis_calls
        synthesis_calls += 1
        return make_llm_call(content=build_synthesis_output("Rerun"))

    with (
        patch(
            "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
            return_value=(extraction, make_llm_call(model="gpt-4.1-nano")),
        ),
        patch(
            "workflows.wiki_synthesis.synthesize.generate_with_usage",
            side_effect=counting_generate,
        ),
    ):
        synthesize_item(item, db_path=wiki_db_path, wiki_dir=wiki_dir)
        assert synthesis_calls == 1
        synthesize_item(item, db_path=wiki_db_path, wiki_dir=wiki_dir)

    assert synthesis_calls == 2
    assert _processed_ids(wiki_db_path, "ok") == {"content_rerun"}
    # Reused entity → one entity, one page, one file (UPDATE not duplicate).
    assert _page_count(wiki_db_path) == 1
    assert len(list(wiki_dir.glob("*.md"))) == 1


def test_synthesize_item_sets_trace_attrs_when_configured(tmp_path: Path, wiki_db_path):
    """With LANGFUSE_PUBLIC_KEY set, the run opens one span named
    wiki_synthesis__<item_id> and stamps session_id + tags on the trace — the
    grouping that gives the nested-trace view in Langfuse."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = ExtractionResult(entities=[])
    fake_client = MagicMock()

    with (
        patch.dict(os.environ, {"LANGFUSE_PUBLIC_KEY": "test_pk"}),
        patch(
            "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
            return_value=(extraction, make_llm_call(model="gpt-4.1-nano")),
        ),
        patch("langfuse.get_client", return_value=fake_client),
    ):
        synthesize_item(_runner_item(), db_path=wiki_db_path, wiki_dir=wiki_dir, replay=True)

    fake_client.start_as_current_span.assert_called_once_with(name="wiki_synthesis__content_runner")
    kwargs = fake_client.update_current_trace.call_args.kwargs
    assert kwargs["name"] == "wiki_synthesis__content_runner"
    assert kwargs["session_id"] == "content_runner"
    assert "wiki_synthesis" in kwargs["tags"]
    assert "raw_store" in kwargs["tags"]
    assert "replay" in kwargs["tags"]


def test_synthesize_item_no_tracing_when_unconfigured(tmp_path: Path, wiki_db_path):
    """No LANGFUSE_PUBLIC_KEY → no Langfuse client touched at all (silent)."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = ExtractionResult(entities=[])
    fake_client = MagicMock()

    saved = os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
    try:
        with (
            patch(
                "workflows.wiki_synthesis.synthesize.generate_structured_with_usage",
                return_value=(extraction, make_llm_call(model="gpt-4.1-nano")),
            ),
            patch("langfuse.get_client", return_value=fake_client),
        ):
            synthesize_item(_runner_item(), db_path=wiki_db_path, wiki_dir=wiki_dir)
    finally:
        if saved is not None:
            os.environ["LANGFUSE_PUBLIC_KEY"] = saved

    fake_client.start_as_current_span.assert_not_called()
    # The empty extraction still records a 'skipped' processed row.
    assert _processed_ids(wiki_db_path, "skipped") == {"content_runner"}
