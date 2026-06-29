"""Tests for the structurer cascade (trafilatura → cloud chain)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fetcher.extractors import structure
from fetcher.extractors.structure import (
    ChainEntry,
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


async def test_run_cascade_routes_clean_markdown_to_cloud_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Markdown the cascade previously returned unchanged as already-clean must
    now reach the cloud chain — pasted content is always LLM-cleaned, never
    returned with boilerplate intact."""
    monkeypatch.setattr(structure, "_stage_trafilatura", lambda _raw: None)

    async def _fake_chain(*_a, **_kw):
        return ("# cleaned body", "structurer:gpt-4.1-mini", {"provider": "openai"})

    monkeypatch.setattr(structure, "_stage_cloud_chain", _fake_chain)

    ctx = _make_ctx()
    ctx.openai_api_key = "sk-test"

    result = await structure.run_cascade(
        ctx,
        raw_content=_AUTHORED_MARKDOWN,
        source_url="https://example.com/a",
        title=None,
        content_date=None,
        author_name=None,
        prompt="SYSTEM PROMPT",
    )

    assert result.markdown == "# cleaned body"
    assert result.tier_used == "structurer:gpt-4.1-mini"
    assert [e.tier for e in result.tier_log] == ["trafilatura", "structurer:gpt-4.1-mini"]


async def test_stage_trafilatura_returns_when_input_is_html_with_clean_body() -> None:
    result = structure._stage_trafilatura(_CLEAN_HTML)
    assert result is not None
    assert len(result) >= 3000
    assert "Real Article" in result


async def test_run_cascade_returns_problem_when_all_stages_produce_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(structure, "_stage_trafilatura", lambda _raw: None)

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
    assert [e.tier for e in err.tier_log] == ["trafilatura", "structurer"]


# --- Cloud chain tests (SF4) ---


def _openai_entry() -> ChainEntry:
    return ChainEntry(model="gpt-4.1-mini", provider="openai", attempt_timeout=60.0)


def _ollama_entry() -> ChainEntry:
    return ChainEntry(
        model="qwen3.5:cloud",
        provider="ollama",
        base_url="https://ollama.com/v1",
        attempt_timeout=20.0,
    )


def _mock_openai_response(content: str) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


async def test_call_cloud_chain_uses_primary_entry_when_both_keys_set() -> None:
    primary_client = MagicMock()
    primary_client.chat.completions.create = AsyncMock(
        return_value=_mock_openai_response("# clean\n\nbody")
    )

    with patch("openai.AsyncOpenAI", return_value=primary_client):
        markdown, tier, usage = await structure.call_cloud_chain(
            "noisy",
            "SYS",
            chain=[_openai_entry(), _ollama_entry()],
            openai_key="sk-openai",
            ollama_key="sk-ollama",
        )

    # Observable: primary entry produced the result (proven by provider/model);
    # ctor.call_count + ctor.call_args.kwargs["api_key"] would over-couple to
    # the AsyncOpenAI() constructor shape — dropped.
    assert markdown == "# clean\n\nbody"
    assert tier == "structurer:gpt-4.1-mini"
    assert usage["provider"] == "openai"
    assert usage["model"] == "gpt-4.1-mini"
    assert "duration_ms" in usage


async def test_call_cloud_chain_falls_to_ollama_on_openai_failure() -> None:
    openai_client = MagicMock()
    openai_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("openai down"))
    ollama_client = MagicMock()
    ollama_client.chat.completions.create = AsyncMock(
        return_value=_mock_openai_response("# from ollama")
    )

    with patch("openai.AsyncOpenAI", side_effect=[openai_client, ollama_client]):
        markdown, tier, usage = await structure.call_cloud_chain(
            "noisy",
            "SYS",
            chain=[_openai_entry(), _ollama_entry()],
            openai_key="sk-openai",
            ollama_key="sk-ollama",
        )

    assert markdown == "# from ollama"
    assert tier == "structurer:qwen3.5:cloud"
    assert usage["provider"] == "ollama"
    assert usage["model"] == "qwen3.5:cloud"


async def test_call_cloud_chain_raises_when_all_entries_exhausted() -> None:
    openai_client = MagicMock()
    openai_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("openai down"))
    ollama_client = MagicMock()
    ollama_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("ollama down"))

    with patch("openai.AsyncOpenAI", side_effect=[openai_client, ollama_client]):
        with pytest.raises(StructurerChainFailed) as excinfo:
            await structure.call_cloud_chain(
                "noisy",
                "SYS",
                chain=[_openai_entry(), _ollama_entry()],
                openai_key="sk-openai",
                ollama_key="sk-ollama",
            )

    assert excinfo.value.retryable is True
    assert "ollama down" in str(excinfo.value)


async def test_call_cloud_chain_raises_retryable_false_when_all_keys_missing() -> None:
    with pytest.raises(StructurerChainFailed) as excinfo:
        await structure.call_cloud_chain(
            "noisy",
            "SYS",
            chain=[_openai_entry(), _ollama_entry()],
            openai_key=None,
            ollama_key=None,
        )

    assert excinfo.value.retryable is False
    assert "no API keys" in str(excinfo.value)


async def test_call_cloud_chain_threads_hint_kwargs_into_user_message() -> None:
    openai_client = MagicMock()
    openai_client.chat.completions.create = AsyncMock(return_value=_mock_openai_response("# out"))

    with patch("openai.AsyncOpenAI", return_value=openai_client):
        await structure.call_cloud_chain(
            "raw paste",
            "SYS",
            chain=[_openai_entry()],
            openai_key="sk-openai",
            ollama_key=None,
            title="Real Title",
            content_date="2026-06-01",
            author_name="Jane Doe",
        )

    call_kwargs = openai_client.chat.completions.create.await_args.kwargs
    messages = call_kwargs["messages"]
    user_msg = next(m for m in messages if m["role"] == "user")
    assert "Real Title" in user_msg["content"]
    assert "Jane Doe" in user_msg["content"]
    assert "2026-06-01" in user_msg["content"]
    assert "raw paste" in user_msg["content"]
    system_msg = next(m for m in messages if m["role"] == "system")
    assert system_msg["content"] == "SYS"
