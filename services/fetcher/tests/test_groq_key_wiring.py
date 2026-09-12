"""The whisper chain's primary provider must be reachable in a real process.

`whisper._key_for` reads `ctx.groq_api_key`. The existing whisper tests build
their context from `MagicMock()`, which answers *any* attribute with a truthy
mock — so they pass whether or not the key is actually plumbed from the
environment. These tests close that gap at the real seam.
"""

import os
from unittest.mock import patch


def test_settings_reads_groq_api_key_from_env() -> None:
    from fetcher.config import Settings

    with patch.dict(os.environ, {"GROQ_API_KEY": "groq-sk-test"}):
        settings = Settings(socks5_url="socks5://x", llama_parse_api_key="x")

    assert settings.groq_api_key == "groq-sk-test"


async def test_fetch_context_carries_groq_api_key() -> None:
    """`whisper._key_for("groq", ctx)` reads this attribute off the live
    context; without it the chain's primary provider is silently unreachable
    and every transcription falls through to the OpenAI fallback."""
    from fetcher.config import Settings
    from fetcher.context import make_fetch_context
    from fetcher.extractors import whisper

    with patch.dict(os.environ, {"GROQ_API_KEY": "groq-sk-test"}):
        settings = Settings(socks5_url="socks5://x", llama_parse_api_key="x")

    async with make_fetch_context(settings) as ctx:
        assert whisper._key_for("groq", ctx) == "groq-sk-test"
