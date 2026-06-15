"""Env-driven settings for the fetcher service.

All vars are prefixed ``FETCHER_`` to avoid colliding with host env. Settings
split three ways by failure shape when unset:

- **Required** (no default; ``Settings()`` raises at boot, healthz returns 503
  with the missing-list): ``FETCHER_SOCKS5_URL``, ``FETCHER_LLAMA_PARSE_API_KEY``.
- **Optional capability** (``str | None`` default ``None``; the dependent tier
  or endpoint becomes unreachable but the service still boots): Jina, Tavily,
  RapidAPI/Medium, plus the structurer's OpenAI / Ollama keys
  (``/v1/structure`` returns 503 ``STRUCTURER_UNCONFIGURED`` when both are
  unset and the deterministic stages produce nothing).
- **Tunables** (default-with-override): cache TTL, batch cap, default timeout,
  LlamaParse tiers, the structurer YAML / prompt paths.

Filesystem-path settings (medium domains, structurer chain config, structurer
prompt) are loaded once at module import — the corresponding extractor /
handler module reads ``os.environ.get(...)`` directly rather than waiting for
``Settings`` so the path is resolved before FastAPI startup runs.
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
    upstream_timeout_s: int = Field(
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
        default="fast",
        description="LlamaParse tier used for the generic PDF handler.",
    )
    tavily_api_key: str | None = Field(
        default=None,
        description=(
            "Tavily Extract API key. Optional: the article handler's tavily tier "
            "is unreachable when unset."
        ),
    )
    rapidapi_key: str | None = Field(
        default=None,
        description=(
            "RapidAPI key. Powers Medium paywall bypass (mediumapi.com), the "
            "Facebook post handler (facebook-scraper-api4 → facebook-scraper3 "
            "cascade), and the YouTube captions fallback (youtube-data16) "
            "fired when the free transcript_api tier is IP-blocked even via "
            "the SOCKS5 proxy. Optional, but Facebook URLs fail with 502 "
            "when unset (handler is STRICT_PAID_TIER)."
        ),
    )
    medium_domains_path: str = Field(
        default="config/medium_domains.yaml",
        description=(
            "Path to the YAML file listing Medium-hosted domains. "
            "Relative to the service working dir."
        ),
    )
    openai_api_key: str | None = Field(
        default=None,
        description=(
            "OpenAI API key for the /v1/structure cloud LLM chain. "
            "Optional individually; at least one of openai/ollama is needed for "
            "the cloud stage to function."
        ),
    )
    ollama_api_key: str | None = Field(
        default=None,
        description=(
            "Ollama Cloud API key for the /v1/structure fallback. "
            "Used with the OpenAI-compat base_url declared in structurer.yaml."
        ),
    )
    structurer_config_path: str = Field(
        default="config/structurer.yaml",
        description="Path to the YAML file declaring the structurer cloud chain.",
    )
    structurer_prompt_path: str = Field(
        default="prompts/structure_v1.md",
        description="Path to the active /v1/structure system prompt.",
    )
    transcript_structurer_config_path: str = Field(
        default="config/transcript_structurer.yaml",
        description="Path to the YAML file declaring the transcript structurer cloud chain.",
    )
    transcript_structurer_prompt_path: str = Field(
        default="prompts/structure_transcript_v1.md",
        description="Path to the active transcript structurer system prompt.",
    )
    youtube_structurer_enabled: bool = Field(
        default=True,
        description=(
            "When True, the YouTube handler runs the cloud transcript structurer "
            "after fetching auto-captions; falls back to raw transcript markdown on "
            "structurer failure. Set False to opt out per-deploy."
        ),
    )
