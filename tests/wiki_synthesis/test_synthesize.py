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
from workflows.wiki_synthesis.synthesize import synthesize_from_candidates, synthesize_item

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
            _runner_item(),
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
    item = make_item(item_id="content_rerun", source_ref="raw_store:content_rerun")
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
