"""Tests for the source-agnostic transcript structurer module.

Reuses the shared `_cloud_chain.call_cloud_chain` runner; tests focus on the
wrapper that loads the transcript-specific chain + prompt and the public
`structure_transcript(ctx, raw_markdown, *, title, author)` surface.
"""

from unittest.mock import MagicMock

import pytest

from fetcher.extractors import transcript_structurer
from fetcher.extractors._cloud_chain import ChainEntry, StructurerChainFailed


def _make_ctx(*, openai_key: str | None = "openai-key", ollama_key: str | None = "ollama-key"):
    ctx = MagicMock()
    ctx.openai_api_key = openai_key
    ctx.ollama_api_key = ollama_key
    return ctx


_RAW = "speaker one talks about stuff and another voice replies and they go on " * 50


@pytest.fixture
def freeze_chain(monkeypatch: pytest.MonkeyPatch) -> list[ChainEntry]:
    chain = [
        ChainEntry(
            model="gemma4:31b",
            provider="ollama",
            base_url="https://ollama.com/v1",
            attempt_timeout=240.0,
        ),
        ChainEntry(model="gpt-4.1-mini", provider="openai", attempt_timeout=120.0),
    ]
    monkeypatch.setattr(transcript_structurer, "_CHAIN", chain)
    return chain


async def test_structure_transcript_threads_title_and_author_into_user_message_not_system(
    freeze_chain: list[ChainEntry], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hint context (title/author) must land in the USER message, never the system prompt
    — preserves OpenAI prompt cache invariant: identical system prompt across calls."""
    monkeypatch.setattr(transcript_structurer, "_PROMPT", "you are a transcript structurer")

    captured = {}

    async def fake_chain(content, prompt, **kwargs):
        captured["content"] = content
        captured["prompt"] = prompt
        captured["title"] = kwargs.get("title")
        captured["author_name"] = kwargs.get("author_name")
        return "Structured paragraph.", "structurer:gemma4:31b", {"tokens_in": 1, "tokens_out": 2}

    monkeypatch.setattr(transcript_structurer, "call_cloud_chain", fake_chain)

    ctx = _make_ctx()
    markdown, tier, _ = await transcript_structurer.structure_transcript(
        ctx, _RAW, title="My Talk", author="Jane Doe"
    )

    assert markdown == "Structured paragraph."
    assert tier == "structurer:gemma4:31b"
    assert captured["prompt"] == "you are a transcript structurer"
    assert "My Talk" not in captured["prompt"]
    assert "Jane Doe" not in captured["prompt"]
    assert captured["title"] == "My Talk"
    assert captured["author_name"] == "Jane Doe"


async def test_structure_transcript_omits_hints_when_title_and_author_none(
    freeze_chain: list[ChainEntry], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Degrade gracefully when oembed didn't return title/author."""
    monkeypatch.setattr(transcript_structurer, "_PROMPT", "sys prompt")

    captured = {}

    async def fake_chain(content, prompt, **kwargs):
        captured["title"] = kwargs.get("title")
        captured["author_name"] = kwargs.get("author_name")
        return "ok", "structurer:gemma4:31b", {}

    monkeypatch.setattr(transcript_structurer, "call_cloud_chain", fake_chain)

    ctx = _make_ctx()
    await transcript_structurer.structure_transcript(ctx, _RAW, title=None, author=None)

    assert captured["title"] is None
    assert captured["author_name"] is None


async def test_structure_transcript_propagates_chain_failure(
    freeze_chain: list[ChainEntry], monkeypatch: pytest.MonkeyPatch
) -> None:
    """All chain entries failed → caller (handler) decides fallback strategy."""
    monkeypatch.setattr(transcript_structurer, "_PROMPT", "p")

    async def fake_chain(*args, **kwargs):
        raise StructurerChainFailed("upstream timeout", retryable=True)

    monkeypatch.setattr(transcript_structurer, "call_cloud_chain", fake_chain)

    ctx = _make_ctx()
    with pytest.raises(StructurerChainFailed, match="upstream timeout"):
        await transcript_structurer.structure_transcript(ctx, _RAW, title="t", author="a")


async def test_structure_transcript_uses_module_chain_and_prompt(
    freeze_chain: list[ChainEntry], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the module-level _CHAIN and _PROMPT actually thread into call_cloud_chain."""
    monkeypatch.setattr(transcript_structurer, "_PROMPT", "transcript-system-prompt")

    captured = {}

    async def fake_chain(content, prompt, *, chain, openai_key, ollama_key, **kwargs):
        captured["chain"] = chain
        captured["openai_key"] = openai_key
        captured["ollama_key"] = ollama_key
        captured["prompt"] = prompt
        return "md", "structurer:gemma4:31b", {}

    monkeypatch.setattr(transcript_structurer, "call_cloud_chain", fake_chain)

    ctx = _make_ctx(openai_key="oai", ollama_key="oll")
    await transcript_structurer.structure_transcript(ctx, _RAW, title=None, author=None)

    assert captured["chain"] == freeze_chain
    assert captured["openai_key"] == "oai"
    assert captured["ollama_key"] == "oll"
    assert captured["prompt"] == "transcript-system-prompt"


def test_get_chain_returns_a_copy() -> None:
    """Defensive: caller mutating return value must not affect module state."""
    chain = transcript_structurer.get_chain()
    chain.append(ChainEntry(model="x", provider="openai"))
    assert ChainEntry(model="x", provider="openai") not in transcript_structurer.get_chain()


def test_get_prompt_returns_in_memory_prompt() -> None:
    # Just calls — content depends on whether config file is present;
    # we only assert it's a string (possibly empty if prompt file is absent).
    assert isinstance(transcript_structurer.get_prompt(), str)
