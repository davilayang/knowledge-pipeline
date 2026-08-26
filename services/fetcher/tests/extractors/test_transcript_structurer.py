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


async def test_structure_transcript_keeps_hints_out_of_system_prompt(
    freeze_chain: list[ChainEntry], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user-cache invariant: title/author must NOT leak into the system
    prompt (which OpenAI caches), only into the user message. Cache hit-rate
    depends on the system prompt being byte-identical across calls."""
    monkeypatch.setattr(transcript_structurer, "_PROMPT", "you are a transcript structurer")

    captured_prompt: list[str] = []

    async def fake_chain(content, prompt, **kwargs):
        captured_prompt.append(prompt)
        return "Structured paragraph.", "structurer:gemma4:31b", {}

    monkeypatch.setattr(transcript_structurer, "call_cloud_chain", fake_chain)

    ctx = _make_ctx()
    markdown, tier, _ = await transcript_structurer.structure_transcript(
        ctx, _RAW, title="My Talk", author="Jane Doe"
    )

    assert markdown == "Structured paragraph."
    assert tier == "structurer:gemma4:31b"
    assert captured_prompt == ["you are a transcript structurer"]
    assert "My Talk" not in captured_prompt[0]
    assert "Jane Doe" not in captured_prompt[0]


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


def test_get_chain_returns_a_copy() -> None:
    """Defensive: caller mutating return value must not affect module state."""
    chain = transcript_structurer.get_chain()
    chain.append(ChainEntry(model="x", provider="openai"))
    assert ChainEntry(model="x", provider="openai") not in transcript_structurer.get_chain()


async def test_long_transcript_is_split_into_chunks_and_rejoined(
    freeze_chain: list[ChainEntry], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Above roughly 50k characters the model summarises instead of structuring:
    one production transcript retains 19-41% whole, 56% as a half and 98% as a
    quarter. Splitting the input keeps every segment inside the range where the
    model transcribes rather than compresses."""
    monkeypatch.setattr(transcript_structurer, "_PROMPT", "you are a transcript structurer")
    sent: list[str] = []

    async def fake_chain(content, prompt, **kwargs):
        sent.append(content)
        return f"[segment {len(sent)}]", "structurer:gemma4:31b", {}

    monkeypatch.setattr(transcript_structurer, "call_cloud_chain", fake_chain)

    body = "word " * 12_000  # 60,000 chars -> three segments at a 25k limit
    markdown, _tier, _usage = await transcript_structurer.structure_transcript(
        _make_ctx(), body, title=None, author=None
    )

    assert len(sent) == 3
    assert all(len(s) <= 25_000 for s in sent)
    assert markdown == "[segment 1]\n\n[segment 2]\n\n[segment 3]"
