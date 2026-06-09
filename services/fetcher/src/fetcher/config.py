"""Env-driven settings for the fetcher service.

All vars are prefixed ``FETCHER_`` to avoid colliding with host env.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FETCHER_",
        env_file=None,
        extra="ignore",
    )

    db_path: str = Field(
        default="/app/data/fetches.db",
        description="SQLite database path for fetcher cache, fetch jobs, and URL aliases.",
    )
    cache_ttl_days: int = Field(
        default=365,
        description="Default number of days before cached fetcher rows expire.",
    )
    batch_max: int = Field(
        default=100,
        description="Maximum number of URLs accepted in a batch fetch request.",
    )
    default_timeout_s: int = Field(
        default=30,
        description="Default upstream request timeout in seconds.",
    )

    jina_api_key: str | None = Field(
        default=None,
        description=(
            "Jina Reader API key. Optional: the free tier works without auth at lower rate limits. "
            "Set this to unlock the paid tier's higher quota."
        ),
    )
    socks5_url: str = Field(description="SOCKS5 proxy URL used for browser-like fetch fallbacks.")
    llama_parse_api_key: str = Field(description="LlamaParse API key used by PDF/arXiv tiers.")
    llama_parse_tier_arxiv: str = Field(
        default="agentic_plus",
        description="LlamaParse tier used for arXiv PDF rendering.",
    )
    llama_parse_tier_pdf: str = Field(
        default="agentic_plus",
        description="LlamaParse tier used for the generic PDF handler.",
    )
    tavily_api_key: str | None = Field(
        default=None,
        description=(
            "Tavily Extract API key. Optional: the article handler's tavily tier "
            "is unreachable when unset."
        ),
    )
