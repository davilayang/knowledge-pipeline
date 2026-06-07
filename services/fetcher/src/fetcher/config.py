"""Env-driven settings for the fetcher service.

All vars are prefixed ``FETCHER_`` to avoid colliding with host env.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FETCHER_",
        env_file=None,
        extra="ignore",
    )

    db_path: str = "/app/data/fetcher.db"
    cache_ttl_days: int = 365
    batch_max: int = 100
    default_timeout_s: int = 30

    jina_api_key: str
    socks5_url: str
    llama_parse_api_key: str
    llama_parse_tier_arxiv: str = "agentic_plus"
