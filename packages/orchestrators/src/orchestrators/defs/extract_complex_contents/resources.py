"""Resources for the extract_complex_contents pipeline.

- FetcherResource — per-type dispatch (YouTube, arXiv, article cascade).
- ExtractorRegistry — strategy registry: maps content_type → ExtractorProtocol.

Notion access and the queue.db wrapper live in
`orchestrators.defs.shared.queue_resources` since both are shared with the
triage pipeline. This module wires the extract-specific resources and
binds shared classes to local keys in `build_resources`.

Per-type strategies live in `fetchers/` and `extractors/`; this module
holds the Dagster ConfigurableResource boundaries that orchestrate them.
"""

import hashlib
from pathlib import Path
from typing import Any

import dagster as dg

from orchestrators.defs.shared.queue_resources import (
    NotionQueueResource,
    QueueStoreResource,
)

from .extractors import ExtractionUsage, ExtractorProtocol, SingleShotOpenAIExtractor
from .fetchers import FetchResult  # noqa: F401 — re-exported for callers

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


class FetcherResource(dg.ConfigurableResource):
    pi_socks5_url: str
    impersonate_profile: str = "safari17_0"
    jina_floor_chars: int = 2000
    timeout_s: int = 30
    youtube_proxy_url: str = ""
    # LlamaParse (LlamaCloud) for arxiv PDF rendering. kp uses agentic_plus —
    # the highest-quality tier — because the async ingestion layer accepts
    # the ~60s/26-page latency cost in exchange for cleaner Topic Cards.
    llama_cloud_api_key: str = ""
    llama_cloud_base_url: str = "https://api.cloud.eu.llamaindex.ai"
    llama_parse_tier_arxiv: str = "agentic_plus"

    def fetch_for_type(self, url: str, *, content_type: str) -> FetchResult:
        """Dispatch to per-type fetcher. Defensive fallback to article cascade
        for unknown types — should only trigger when triage misclassifies."""
        if content_type == "YouTube":
            from .fetchers import youtube

            return youtube.fetch(url, proxy_url=self.youtube_proxy_url or None)
        if content_type == "arXiv":
            from .fetchers import arxiv as arxiv_fetcher

            return arxiv_fetcher.fetch(
                url,
                llama_cloud_api_key=self.llama_cloud_api_key,
                llama_cloud_base_url=self.llama_cloud_base_url,
                llama_parse_tier=self.llama_parse_tier_arxiv,
            )
        from .fetchers import article

        return article.fetch(
            url,
            pi_socks5_url=self.pi_socks5_url,
            impersonate_profile=self.impersonate_profile,
            jina_floor_chars=self.jina_floor_chars,
            timeout_s=self.timeout_s,
        )

    def fetch(self, url: str) -> FetchResult:
        return self.fetch_for_type(url, content_type="Article")


class ExtractorRegistry(dg.ConfigurableResource):
    """Strategy registry — maps content_type → ExtractorProtocol impl.

    v1: same SingleShotOpenAIExtractor backend for every type, only the
    prompt differs. Future: adopting LangGraph for arXiv means writing
    a class in `extractors/langgraph_arxiv.py` implementing
    ExtractorProtocol and changing `_strategy_for("arXiv")` to return
    it — no asset edits required.
    """

    openai_api_key: str
    model: str
    prompt_label_article: str
    prompt_label_youtube: str
    prompt_label_arxiv: str
    max_tokens: int = 2048

    def _label_for(self, content_type: str) -> str:
        return {
            "YouTube": self.prompt_label_youtube,
            "arXiv": self.prompt_label_arxiv,
            "Article": self.prompt_label_article,
        }.get(content_type, self.prompt_label_article)

    def _prompt_path(self, content_type: str) -> Path:
        return _PROMPTS_DIR / f"{self._label_for(content_type)}.md"

    def _prompt_text(self, content_type: str) -> str:
        return self._prompt_path(content_type).read_text()

    def prompt_label(self, content_type: str) -> str:
        return self._label_for(content_type)

    def prompt_sha256(self, content_type: str) -> str:
        return hashlib.sha256(self._prompt_text(content_type).encode()).hexdigest()

    def _strategy_for(self, content_type: str) -> ExtractorProtocol:
        return SingleShotOpenAIExtractor(
            api_key=self.openai_api_key,
            model=self.model,
            prompt_text=self._prompt_text(content_type),
            max_tokens=self.max_tokens,
        )

    def extract(self, content: str, *, content_type: str) -> tuple[dict[str, Any], ExtractionUsage]:
        return self._strategy_for(content_type).extract(content, content_type=content_type)


def build_resources() -> dict[str, dg.ConfigurableResource]:
    return {
        "notion": NotionQueueResource(
            integration_token=dg.EnvVar("NOTION_INTEGRATION_TOKEN"),
            queue_db_id=dg.EnvVar("NOTION_QUEUE_DB_ID"),
            queue_data_source_id=dg.EnvVar("NOTION_QUEUE_DATA_SOURCE_ID"),
        ),
        "fetcher": FetcherResource(
            pi_socks5_url=dg.EnvVar("PI_SOCKS5_URL"),
            impersonate_profile=dg.EnvVar("EXTRACT_QUEUE_IMPERSONATE_PROFILE"),
            youtube_proxy_url=dg.EnvVar("YOUTUBE_PROXY_URL").get_value(""),
            llama_cloud_api_key=dg.EnvVar("LLAMA_CLOUD_API_KEY"),
        ),
        "extractor": ExtractorRegistry(
            openai_api_key=dg.EnvVar("OPENAI_API_KEY"),
            model=dg.EnvVar("EXTRACT_QUEUE_MODEL"),
            prompt_label_article=dg.EnvVar("EXTRACT_QUEUE_PROMPT_LABEL_ARTICLE"),
            prompt_label_youtube=dg.EnvVar("EXTRACT_QUEUE_PROMPT_LABEL_YOUTUBE"),
            prompt_label_arxiv=dg.EnvVar("EXTRACT_QUEUE_PROMPT_LABEL_ARXIV"),
        ),
        "store": QueueStoreResource(),
    }
