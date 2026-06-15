"""Tests for shared cloud-chain primitives + cache-key helpers in _cloud_chain.py.

PR 1 (Phase A) of the structurer-platform plan. These helpers will be reused by
both /v1/structure (article) and the upcoming /v1/structure-transcript endpoint,
and they fix a latent under-keyed cache bug in /v1/structure: today's key omits
prompt content + chain config + hint context, so editing prompts/structure_v1.md
silently fails to invalidate cache.
"""

from fetcher.extractors._cloud_chain import (
    ChainEntry,
    StructurerChainFailed,
    cache_key_components,
    chain_config_sha,
    content_sha,
    prompt_sha,
)


def test_content_sha_is_stable_for_same_input() -> None:
    a = content_sha("hello world")
    b = content_sha("hello world")
    assert a == b
    assert len(a) == 64  # sha256 hex digest


def test_content_sha_changes_when_content_changes() -> None:
    assert content_sha("hello world") != content_sha("hello world!")


def test_prompt_sha_is_an_alias_over_content_sha() -> None:
    """prompt_sha + content_sha are intentionally aliases so call sites read clearly.
    This test exists only to catch accidental divergence (someone making prompt_sha
    a meaningfully different function); the hashing-text behaviour itself is covered
    by test_content_sha_*."""
    assert prompt_sha("any text") == content_sha("any text")


def test_prompt_sha_handles_empty_string() -> None:
    """Empty prompt (file-missing path returns "" from _load_prompt) is well-defined."""
    sha_empty = prompt_sha("")
    assert len(sha_empty) == 64


def test_chain_config_sha_is_stable_for_same_chain() -> None:
    chain = [
        ChainEntry(model="gemma4:31b", provider="ollama", base_url="https://ollama.com/v1"),
        ChainEntry(model="gpt-4.1-mini", provider="openai", attempt_timeout=60.0),
    ]
    assert chain_config_sha(chain) == chain_config_sha(chain)


def test_chain_config_sha_changes_when_model_changes() -> None:
    chain_a = [ChainEntry(model="gpt-4.1-mini", provider="openai")]
    chain_b = [ChainEntry(model="gpt-4o-mini", provider="openai")]
    assert chain_config_sha(chain_a) != chain_config_sha(chain_b)


def test_chain_config_sha_changes_when_order_changes() -> None:
    """Reordering chain entries changes which model is primary → different cache."""
    a = ChainEntry(model="gpt-4.1-mini", provider="openai")
    b = ChainEntry(model="gemma4:31b", provider="ollama", base_url="https://ollama.com/v1")
    assert chain_config_sha([a, b]) != chain_config_sha([b, a])


def test_chain_config_sha_changes_when_attempt_timeout_changes() -> None:
    chain_a = [ChainEntry(model="gpt-4.1-mini", provider="openai", attempt_timeout=30.0)]
    chain_b = [ChainEntry(model="gpt-4.1-mini", provider="openai", attempt_timeout=60.0)]
    assert chain_config_sha(chain_a) != chain_config_sha(chain_b)


def test_chain_config_sha_changes_when_base_url_changes() -> None:
    chain_a = [ChainEntry(model="m", provider="ollama", base_url="https://a/")]
    chain_b = [ChainEntry(model="m", provider="ollama", base_url="https://b/")]
    assert chain_config_sha(chain_a) != chain_config_sha(chain_b)


def test_cache_key_components_is_deterministic() -> None:
    out = cache_key_components(
        endpoint="structure",
        content_sha_value="a" * 64,
        prompt_sha_value="b" * 64,
        chain_config_sha_value="c" * 64,
    )
    assert out == cache_key_components(
        endpoint="structure",
        content_sha_value="a" * 64,
        prompt_sha_value="b" * 64,
        chain_config_sha_value="c" * 64,
    )


def test_cache_key_components_changes_when_prompt_changes() -> None:
    """The bug PR 1 fixes: today /v1/structure ignores prompt content in cache key."""
    k1 = cache_key_components(
        endpoint="structure",
        content_sha_value="a" * 64,
        prompt_sha_value="b" * 64,
        chain_config_sha_value="c" * 64,
    )
    k2 = cache_key_components(
        endpoint="structure",
        content_sha_value="a" * 64,
        prompt_sha_value="DIFFERENT" + "b" * 55,
        chain_config_sha_value="c" * 64,
    )
    assert k1 != k2


def test_cache_key_components_changes_when_chain_config_changes() -> None:
    k1 = cache_key_components(
        endpoint="structure",
        content_sha_value="a" * 64,
        prompt_sha_value="b" * 64,
        chain_config_sha_value="c" * 64,
    )
    k2 = cache_key_components(
        endpoint="structure",
        content_sha_value="a" * 64,
        prompt_sha_value="b" * 64,
        chain_config_sha_value="DIFFERENT" + "c" * 55,
    )
    assert k1 != k2


def test_cache_key_components_changes_when_content_changes() -> None:
    k1 = cache_key_components(
        endpoint="structure",
        content_sha_value="a" * 64,
        prompt_sha_value="b" * 64,
        chain_config_sha_value="c" * 64,
    )
    k2 = cache_key_components(
        endpoint="structure",
        content_sha_value="DIFFERENT" + "a" * 55,
        prompt_sha_value="b" * 64,
        chain_config_sha_value="c" * 64,
    )
    assert k1 != k2


def test_cache_key_components_namespaces_by_endpoint() -> None:
    """structure vs structure-transcript must not collide even with identical content."""
    k1 = cache_key_components(
        endpoint="structure",
        content_sha_value="a" * 64,
        prompt_sha_value="b" * 64,
        chain_config_sha_value="c" * 64,
    )
    k2 = cache_key_components(
        endpoint="structure-transcript",
        content_sha_value="a" * 64,
        prompt_sha_value="b" * 64,
        chain_config_sha_value="c" * 64,
    )
    assert k1 != k2


def test_build_user_message_prepends_hints_above_content() -> None:
    """Title/author/date go INTO the user message (not the system prompt).
    The hint block sits above the content separated by `---` so the LLM sees
    structured context before the raw transcript/article."""
    from fetcher.extractors._cloud_chain import _build_user_message

    out = _build_user_message(
        "raw body text",
        title="My Talk",
        content_date="2026-06-14",
        author_name="Jane Doe",
    )
    assert out == "Title: My Talk\nAuthor: Jane Doe\nDate: 2026-06-14\n\n---\n\nraw body text"


def test_build_user_message_omits_hint_block_entirely_when_all_none() -> None:
    """No hints → no hint block → user message is the raw content unchanged.
    Important: a stray `---\\n\\n` prefix would confuse downstream LLMs."""
    from fetcher.extractors._cloud_chain import _build_user_message

    out = _build_user_message("raw body text", title=None, content_date=None, author_name=None)
    assert out == "raw body text"


def test_build_user_message_skips_missing_hint_lines() -> None:
    """Only the hints actually present appear in the block."""
    from fetcher.extractors._cloud_chain import _build_user_message

    out = _build_user_message("body", title="Only Title", content_date=None, author_name=None)
    assert out == "Title: Only Title\n\n---\n\nbody"


def test_re_exported_from_structure_for_backward_compat() -> None:
    """structure.py re-exports ChainEntry/StructurerChainFailed for existing imports."""
    from fetcher.extractors.structure import ChainEntry as RE_ChainEntry
    from fetcher.extractors.structure import StructurerChainFailed as RE_StructurerChainFailed

    assert RE_ChainEntry is ChainEntry
    assert RE_StructurerChainFailed is StructurerChainFailed


async def test_call_cloud_chain_raises_not_configured_on_empty_chain() -> None:
    """Empty chain (yaml not loaded, e.g. fetcher image missing config/) raises
    StructurerNotConfigured with a message that names the actual condition —
    NOT the generic 'no API keys configured'. This is the bug that escaped
    diagnosis for hours: silent FileNotFoundError → empty chain → misleading
    error pointing at keys when the real cause was missing config files."""
    from fetcher.extractors._cloud_chain import StructurerNotConfigured, call_cloud_chain

    try:
        await call_cloud_chain(
            "content", "prompt", chain=[], openai_key="sk-set", ollama_key="ol-set"
        )
        raise AssertionError("expected StructurerNotConfigured")
    except StructurerNotConfigured as exc:
        assert "chain config not loaded" in str(exc)
        assert exc.retryable is False


async def test_call_cloud_chain_raises_not_configured_when_no_provider_has_key() -> None:
    """Chain has entries but no provider's API key is set → StructurerNotConfigured
    with the distinct 'no API keys configured for any chain provider' message.
    Symmetric to the empty-chain case via a single StructurerNotConfigured
    subclass — endpoints map both to 503 via isinstance."""
    from fetcher.extractors._cloud_chain import StructurerNotConfigured, call_cloud_chain

    chain = [
        ChainEntry(model="m1", provider="openai"),
        ChainEntry(model="m2", provider="ollama"),
    ]
    try:
        await call_cloud_chain("content", "prompt", chain=chain, openai_key=None, ollama_key=None)
        raise AssertionError("expected StructurerNotConfigured")
    except StructurerNotConfigured as exc:
        assert "no API keys configured for any chain provider" in str(exc)
        assert exc.retryable is False


def test_not_configured_is_a_chain_failed_subclass() -> None:
    """Endpoint handlers catch StructurerChainFailed broadly, then check
    isinstance(exc, StructurerNotConfigured) for the 503 branch. The subclass
    relationship is load-bearing — assert it explicitly."""
    from fetcher.extractors._cloud_chain import StructurerNotConfigured

    assert issubclass(StructurerNotConfigured, StructurerChainFailed)
