"""End-to-end test for the runner helper.

The lower-level parity tests in test_graph.py exercise the workflow
without a checkpointer or Langfuse config. This test verifies the
runner's bundled invocation actually works against a real Postgres
checkpoint store and propagates Langfuse callbacks at graph.invoke
level (not just per-LLM-call).
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from domains.wiki.state import get_page, get_processed_ids
from domains.wiki.types import ExtractedEntity, ExtractionResult
from tests.wiki_synthesis._helpers import make_item
from workflows.shared.observability import get_langfuse_callback
from workflows.wiki_synthesis.runner import invoke_wiki_synthesis


def _runner_item():
    """Stable-id item for runner tests; tests assert against this item_id."""
    return make_item(
        item_id="content_runner",
        title="Runner Test Article",
        text="# Test\n\nA test article.",
        source_ref="raw_store:content_runner",
    )


@pytest.fixture(autouse=True)
def _reset_langfuse_cache():
    """get_langfuse_callback uses lru_cache; tests that toggle env vars must
    start from a clean cache or they see the previous test's resolved value."""
    get_langfuse_callback.cache_clear()
    yield
    get_langfuse_callback.cache_clear()


def test_runner_invokes_workflow_end_to_end(tmp_path: Path, wiki_pg, wiki_pg_url):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = ExtractionResult(
        entities=[
            ExtractedEntity(
                entity_id="concept__test",
                title="Test",
                page_type="concept",
                is_new=True,
            )
        ]
    )
    llm_output = (
        "---\n"
        "entity_id: concept__test\n"
        "title: Test\n"
        "page_type: concept\n"
        "---\n"
        "# Test\n\nBody."
    )

    with (
        patch(
            "workflows.wiki_synthesis.nodes.generate_structured",
            return_value=extraction,
        ),
        patch(
            "workflows.wiki_synthesis.entity_graph.generate",
            return_value=llm_output,
        ),
    ):
        invoke_wiki_synthesis(_runner_item(), db_url=wiki_pg_url, wiki_dir=wiki_dir)

    assert (wiki_dir / "concept" / "test.md").exists()
    assert get_processed_ids(wiki_pg, status="ok") == {"content_runner"}
    assert get_page(wiki_pg, "concept__test") is not None


def test_runner_passes_langfuse_metadata_when_callback_configured(tmp_path: Path, wiki_pg_url):
    """When LANGFUSE_PUBLIC_KEY is set, the runner should attach the callback
    AND the session_id metadata at graph.invoke level — that's what gives
    the nested-trace view in Langfuse instead of orphan per-LLM-call traces.
    """
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    captured_configs: list[dict] = []

    def fake_invoke(self, state, config=None, **kwargs):
        captured_configs.append(config)
        return {}

    extraction = ExtractionResult(entities=[])

    with (
        patch.dict(
            os.environ,
            {
                "LANGFUSE_PUBLIC_KEY": "test_pk",
                "LANGFUSE_SECRET_KEY": "test_sk",
                "LANGFUSE_HOST": "https://test.langfuse.com",
            },
        ),
        patch(
            "workflows.wiki_synthesis.nodes.generate_structured",
            return_value=extraction,
        ),
        patch("langgraph.pregel.Pregel.invoke", new=fake_invoke),
    ):
        # The autouse fixture cleared the cache once before the test started,
        # but the env patch happens after that. Clear again now so the next
        # callback resolution sees the patched env.
        get_langfuse_callback.cache_clear()
        invoke_wiki_synthesis(_runner_item(), db_url=wiki_pg_url, wiki_dir=wiki_dir, replay=True)

    assert len(captured_configs) == 1
    cfg = captured_configs[0]
    assert cfg["configurable"]["thread_id"] == "wiki_synthesis__content_runner"
    assert len(cfg["callbacks"]) == 1
    assert cfg["metadata"]["langfuse_session_id"] == "content_runner"
    assert "wiki_synthesis" in cfg["metadata"]["langfuse_tags"]
    assert "raw_store" in cfg["metadata"]["langfuse_tags"]
    assert "replay" in cfg["metadata"]["langfuse_tags"]


def test_runner_omits_callback_when_unconfigured(tmp_path: Path, wiki_pg_url):
    """No LANGFUSE_PUBLIC_KEY → callbacks list is empty (no warning, no
    no-op handler). Avoids cluttering tests/local dev with disabled-client
    warnings from langfuse."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    captured_configs: list[dict] = []

    def fake_invoke(self, state, config=None, **kwargs):
        captured_configs.append(config)
        return {}

    extraction = ExtractionResult(entities=[])

    # Make sure LANGFUSE_PUBLIC_KEY is not in the env for this test
    saved = os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
    try:
        with (
            patch(
                "workflows.wiki_synthesis.nodes.generate_structured",
                return_value=extraction,
            ),
            patch("langgraph.pregel.Pregel.invoke", new=fake_invoke),
        ):
            get_langfuse_callback.cache_clear()
            invoke_wiki_synthesis(_runner_item(), db_url=wiki_pg_url, wiki_dir=wiki_dir)
    finally:
        if saved is not None:
            os.environ["LANGFUSE_PUBLIC_KEY"] = saved

    assert captured_configs[0]["callbacks"] == []
    assert captured_configs[0]["metadata"]["langfuse_session_id"] == "content_runner"
