"""Tests for shared cloud-chain primitives + cache-key helpers in _cloud_chain.py.

PR 1 (Phase A) of the structurer-platform plan. These helpers will be reused by
both /v1/structure (article) and the upcoming /v1/structure-transcript endpoint,
and they fix a latent under-keyed cache bug in /v1/structure: today's key omits
prompt content + chain config + hint context, so editing prompts/structure_v1.md
silently fails to invalidate cache.
"""

from pathlib import Path

import pytest

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


def test_prompt_sha_hashes_text() -> None:
    sha_a = prompt_sha("you are a structurer")
    assert len(sha_a) == 64
    assert sha_a != prompt_sha("you are a different structurer")
    assert sha_a == prompt_sha("you are a structurer")


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


def test_cache_key_is_a_string_that_can_serve_as_canonical_url_in_cache_table() -> None:
    """The cache table indexes by canonical_url (TEXT). Key must be string-shaped."""
    key = cache_key_components(
        endpoint="structure",
        content_sha_value="a" * 64,
        prompt_sha_value="b" * 64,
        chain_config_sha_value="c" * 64,
    )
    assert isinstance(key, str)
    assert key.startswith("structure:")
    assert len(key) < 512  # well under sqlite TEXT practical limits


def test_structurer_chain_failed_exposes_retryable_flag() -> None:
    exc = StructurerChainFailed("upstream timeout", retryable=True)
    assert exc.retryable is True
    assert str(exc) == "upstream timeout"

    exc2 = StructurerChainFailed("no API keys", retryable=False)
    assert exc2.retryable is False


def test_chain_entry_is_importable_from_cloud_chain_module() -> None:
    """Both structure.py (article) and transcript_structurer.py will import from here."""
    entry = ChainEntry(model="m", provider="openai")
    assert entry.model == "m"
    assert entry.provider == "openai"
    assert entry.attempt_timeout == 30.0  # default


def test_re_exported_from_structure_for_backward_compat() -> None:
    """structure.py re-exports ChainEntry/StructurerChainFailed for existing imports."""
    from fetcher.extractors.structure import ChainEntry as RE_ChainEntry
    from fetcher.extractors.structure import StructurerChainFailed as RE_StructurerChainFailed

    assert RE_ChainEntry is ChainEntry
    assert RE_StructurerChainFailed is StructurerChainFailed


@pytest.fixture
def tmp_chain_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "structurer.yaml"
    p.write_text(
        """
chain:
  - model: gpt-4.1-mini
    provider: openai
    attempt_timeout: 60.0
"""
    )
    return p


def test_load_chain_moved_to_cloud_chain_module(tmp_chain_yaml: Path) -> None:
    """_load_chain is now sourced from _cloud_chain.py; structure.py re-imports."""
    from fetcher.extractors._cloud_chain import _load_chain

    entries = _load_chain(tmp_chain_yaml)
    assert len(entries) == 1
    assert entries[0].model == "gpt-4.1-mini"
