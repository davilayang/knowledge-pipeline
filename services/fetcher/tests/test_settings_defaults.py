"""Defaults pinned for FETCHER_* settings that have user-visible behaviour.

Catches drift where the in-code default contradicts the operational stance
documented in CLAUDE.md / .env.example / CHANGELOG.
"""


def test_youtube_structurer_enabled_default_is_true() -> None:
    """Phase D flipped the default to True after the E2E smoke confirmed
    real-podcast structuring works end-to-end. Operators can still set
    FETCHER_YOUTUBE_STRUCTURER_ENABLED=false per-deploy to opt out."""
    from fetcher.config import Settings

    settings = Settings(
        socks5_url="socks5://x",
        llama_parse_api_key="x",
    )
    assert settings.youtube_structurer_enabled is True
