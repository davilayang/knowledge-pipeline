"""Tests for the structurer cascade (trafilatura → passthrough → cloud chain stub)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from fetcher.extractors import structure
from fetcher.extractors.structure import (
    StructurerCascadeFailed,
    StructurerChainFailed,
)


_CLEAN_HTML = (
    "<html><body>"
    "<h1>A Real Article</h1>"
    + ("<p>" + ("This is a sentence about a topic. " * 40) + "</p>") * 6
    + "</body></html>"
)


_AUTHORED_MARKDOWN = (
    "# Real Article Title\n\n"
    + ("This is a thoughtful paragraph about an interesting topic. " * 30)
    + "\n\n"
    "## A Section Heading\n\n"
    "- First bullet covering one thing\n"
    "- Second bullet covering another thing\n"
    "- Third bullet for good measure\n\n"
    "Here is a paragraph with **bold one** and **bold two** runs to satisfy the gate.\n\n"
    + ("Another long paragraph of content that makes the document long enough. " * 20)
)


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.openai_api_key = None
    ctx.ollama_api_key = None
    return ctx


async def test_stage_trafilatura_returns_when_input_is_html_with_clean_body() -> None:
    result = structure._stage_trafilatura(_CLEAN_HTML)
    assert result is not None
    assert len(result) >= 3000
    assert "Real Article" in result


async def test_stage_passthrough_recognizes_clean_authored_markdown() -> None:
    result = structure._stage_passthrough_heuristic(_AUTHORED_MARKDOWN)
    assert result == _AUTHORED_MARKDOWN


async def test_stage_passthrough_rejects_single_heading_with_no_other_signals() -> None:
    text = "# Lonely Heading\n\n" + ("A long paragraph with no other markdown signals. " * 80)
    assert structure._stage_passthrough_heuristic(text) is None


async def test_stage_passthrough_rejects_text_with_boilerplate_phrases() -> None:
    text = (
        "# Title\n\n"
        + ("A long paragraph of content. " * 60)
        + "\n\n"
        "## Section\n\n"
        "- One\n- Two\n- Three\n\n"
        "Some **bold** and **other bold** content.\n\n"
        "Subscribe to our newsletter.\n\n"
        "Comments (0)\n\n"
        "Share this article.\n\n"
        + ("More content to satisfy the length floor. " * 40)
    )
    assert structure._stage_passthrough_heuristic(text) is None


async def test_stage_passthrough_allows_occasional_stray_br_tags() -> None:
    text = (
        "# Title\n\n"
        + ("A long paragraph of content. " * 60)
        + "\n\n"
        "## Section<br>\n\n"
        "- One\n- Two\n- Three\n\n"
        "Some **bold** and **other bold** content.<br>\n\n"
        + ("More content to satisfy the length floor. " * 60)
    )
    assert structure._stage_passthrough_heuristic(text) == text


async def test_stage_cloud_chain_called_with_mocked_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_call = AsyncMock(return_value=("# clean md\n\nBody", "structurer:test-model"))
    monkeypatch.setattr(structure, "_call_cloud_chain", mock_call)

    ctx = _make_ctx()
    ctx.openai_api_key = "sk-test"
    result = await structure._stage_cloud_chain(ctx, "noisy plain text", prompt="SYSTEM PROMPT")

    assert result == ("# clean md\n\nBody", "structurer:test-model")
    assert mock_call.await_count == 1


async def test_run_cascade_returns_problem_when_all_stages_produce_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(structure, "_stage_trafilatura", lambda _raw: None)
    monkeypatch.setattr(structure, "_stage_passthrough_heuristic", lambda _raw: None)

    async def _raise(*_a, **_kw):
        raise StructurerChainFailed("all entries failed", retryable=True)

    monkeypatch.setattr(structure, "_stage_cloud_chain", _raise)

    ctx = _make_ctx()
    ctx.openai_api_key = "sk-test"

    with pytest.raises(StructurerCascadeFailed) as excinfo:
        await structure.run_cascade(
            ctx,
            raw_content="noisy input",
            source_url="https://example.com/a",
            title=None,
            content_date=None,
            author_name=None,
            prompt="SYSTEM PROMPT",
        )

    err = excinfo.value
    assert err.retryable is True
    assert err.last_error == "all entries failed"
    assert [e.tier for e in err.tier_log] == ["trafilatura", "passthrough", "structurer"]
