"""The whisper chain's primary provider must be reachable in a real process.

The existing whisper tests build their context from `MagicMock()`, which answers
any attribute with a truthy mock, so they pass whether or not the key is plumbed
from the environment. These assert at the real seam.
"""

import os
from unittest.mock import patch


def test_settings_reads_groq_api_key_from_env() -> None:
    from fetcher.config import Settings

    with patch.dict(os.environ, {"GROQ_API_KEY": "groq-sk-test"}):
        settings = Settings(socks5_url="socks5://x", llama_parse_api_key="x")

    assert settings.groq_api_key == "groq-sk-test"


async def test_fetch_context_carries_groq_api_key() -> None:
    """`_key_for` reads this off the live context; without it the primary is
    silently unreachable and everything falls through to OpenAI."""
    from fetcher.config import Settings
    from fetcher.context import make_fetch_context
    from fetcher.extractors import whisper

    with patch.dict(os.environ, {"GROQ_API_KEY": "groq-sk-test"}):
        settings = Settings(socks5_url="socks5://x", llama_parse_api_key="x")

    async with make_fetch_context(settings) as ctx:
        assert whisper._key_for("groq", ctx) == "groq-sk-test"
