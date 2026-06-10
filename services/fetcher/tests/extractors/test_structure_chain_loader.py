"""Tests for the structurer chain YAML loader."""

from pathlib import Path

import pytest

from fetcher.extractors.structure import ChainEntry, _load_chain


def test_load_chain_parses_yaml(tmp_path: Path) -> None:
    path = tmp_path / "structurer.yaml"
    path.write_text(
        """
chain:
  - model: gpt-4.1-mini
    provider: openai
    attempt_timeout: 60.0
  - model: qwen3.5:cloud
    provider: ollama
    base_url: https://ollama.com/v1
    attempt_timeout: 20.0
"""
    )

    entries = _load_chain(path)

    assert len(entries) == 2
    assert entries[0] == ChainEntry(
        model="gpt-4.1-mini", provider="openai", base_url=None, attempt_timeout=60.0
    )
    assert entries[1] == ChainEntry(
        model="qwen3.5:cloud",
        provider="ollama",
        base_url="https://ollama.com/v1",
        attempt_timeout=20.0,
    )


def test_load_chain_rejects_unknown_provider(tmp_path: Path) -> None:
    path = tmp_path / "structurer.yaml"
    path.write_text(
        """
chain:
  - model: some-model
    provider: not_a_provider
"""
    )

    with pytest.raises(ValueError, match="unknown provider"):
        _load_chain(path)


def test_load_chain_returns_empty_when_file_missing(tmp_path: Path) -> None:
    assert _load_chain(tmp_path / "missing.yaml") == []


def test_load_chain_preserves_order(tmp_path: Path) -> None:
    path = tmp_path / "structurer.yaml"
    path.write_text(
        """
chain:
  - {model: m1, provider: openai}
  - {model: m2, provider: ollama, base_url: https://o.com/v1}
  - {model: m3, provider: openai}
"""
    )
    entries = _load_chain(path)
    assert [e.model for e in entries] == ["m1", "m2", "m3"]
