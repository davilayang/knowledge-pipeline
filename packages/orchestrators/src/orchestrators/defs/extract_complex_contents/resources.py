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

from pathlib import Path

import dagster as dg

from orchestrators.defs.shared.queue_resources import (
    NotionQueueResource,
    QueueStoreResource,
)

from .extractors.three_call_openai import ThreeCallOpenAIExtractor
from .fetchers import FetchResult  # noqa: F401 — re-exported for callers

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


class FetcherResource(dg.ConfigurableResource):
    pi_socks5_url: str
    impersonate_profile: str = "safari17_0"
    jina_floor_chars: int = 2000
    timeout_s: int = 30
    youtube_proxy_url: str = ""
    # LlamaParse (LlamaCloud) for arxiv PDF rendering. Tier is per-deployment
    # via LLAMA_PARSE_TIER: prod runs `agentic_plus` (highest-fidelity, ~60s
    # for a 26-page PDF) — dev defaults to `fast` (layout-only, no LLM,
    # ~100× cheaper) so iterating on the pipeline doesn't burn credits.
    llama_cloud_api_key: str = ""
    llama_cloud_base_url: str = "https://api.cloud.eu.llamaindex.ai"
    llama_parse_tier: str = ""

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
                llama_parse_tier=self.llama_parse_tier,
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
    """Strategy registry — factory for the active ExtractorProtocol impl.

    v2: `ThreeCallOpenAIExtractor` is the only strategy; content-type
    routing now happens **inside** the prompts (each prompt's body branches
    on the `[content_type: ...]` tag the extractor prepends). Future LangGraph
    swap is a one-class change: change `build()` to return `LangGraphExtractor`
    instead — the asset code and storage shape don't move.

    Callers must `build()` ONCE per asset run and reuse the returned
    extractor for `extract()` + reads of `bundle_label` / `bundle_sha256` /
    `model`. Each build constructs a fresh AsyncOpenAI client that's closed
    at the end of `extract()` (see ThreeCallOpenAIExtractor docstring) —
    calling `build()` more than once per run wastes httpx pools and is what
    we explicitly fixed in this commit."""

    openai_api_key: str
    model: str
    prompt_label_narrative: str
    prompt_label_topic_card: str
    prompt_label_followups: str
    max_tokens: int = 2048

    def _prompt_text(self, label: str) -> str:
        return (_PROMPTS_DIR / f"{label}.md").read_text()

    def build(self) -> ThreeCallOpenAIExtractor:
        return ThreeCallOpenAIExtractor(
            api_key=self.openai_api_key,
            model=self.model,
            narrative_prompt=self._prompt_text(self.prompt_label_narrative),
            narrative_prompt_label=self.prompt_label_narrative,
            topic_card_prompt=self._prompt_text(self.prompt_label_topic_card),
            topic_card_prompt_label=self.prompt_label_topic_card,
            followups_prompt=self._prompt_text(self.prompt_label_followups),
            followups_prompt_label=self.prompt_label_followups,
            max_tokens=self.max_tokens,
        )


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
            llama_parse_tier=dg.EnvVar("LLAMA_PARSE_TIER"),
        ),
        "extractor": ExtractorRegistry(
            openai_api_key=dg.EnvVar("OPENAI_API_KEY"),
            model=dg.EnvVar("EXTRACT_QUEUE_MODEL"),
            prompt_label_narrative=dg.EnvVar("EXTRACT_QUEUE_PROMPT_LABEL_NARRATIVE"),
            prompt_label_topic_card=dg.EnvVar("EXTRACT_QUEUE_PROMPT_LABEL_TOPIC_CARD"),
            prompt_label_followups=dg.EnvVar("EXTRACT_QUEUE_PROMPT_LABEL_FOLLOWUPS"),
        ),
        "store": QueueStoreResource(),
    }
