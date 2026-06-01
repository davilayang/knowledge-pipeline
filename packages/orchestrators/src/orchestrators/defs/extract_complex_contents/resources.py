"""Resources for the extract_complex_contents pipeline.

- NotionResource — lifecycle-only writes to the Knowledge OS Queue DB.
- FetcherResource — per-type dispatch (YouTube, arXiv, article cascade).
- ExtractorRegistry — strategy registry: maps content_type → ExtractorProtocol.
- ExtractQueueStore — thin wrapper over domains.raw_store.queue.

Per-type strategies live in `fetchers/` and `extractors/`; this module
holds the Dagster ConfigurableResource boundaries that orchestrate them.
"""

import hashlib
from pathlib import Path
from typing import Any

import dagster as dg
from domains.raw_store import queue as queue_db
from notion_client import Client as NotionClient

from orchestrators.config import LOCAL_QUEUE_DB

from .extractors import ExtractionUsage, ExtractorProtocol, SingleShotOpenAIExtractor
from .fetchers import FetchResult  # noqa: F401 — re-exported for callers

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


class NotionResource(dg.ConfigurableResource):
    integration_token: str
    queue_db_id: str
    queue_data_source_id: str

    def _client(self) -> NotionClient:
        return NotionClient(auth=self.integration_token)

    def query_queue(
        self,
        *,
        page_size: int,
        supported_content_types: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        """Query Notion data source for rows ready for complex-content extraction:
        Status=Fetching AND Content Type ∈ supported_content_types. Triage handles
        classification and flips Status to Fetching before this pipeline picks up.
        """
        type_filters = [
            {"property": "Content Type", "select": {"equals": t}} for t in supported_content_types
        ]
        type_clause = {"or": type_filters} if len(type_filters) > 1 else type_filters[0]
        resp = self._client().data_sources.query(
            data_source_id=self.queue_data_source_id,
            filter={
                "and": [
                    {"property": "Status", "select": {"equals": "Fetching"}},
                    type_clause,
                ]
            },
            page_size=page_size,
        )
        return list(resp.get("results", []))

    def get_status(self, page_id: str) -> str | None:
        page = self._client().pages.retrieve(page_id=page_id)
        status_prop = page.get("properties", {}).get("Status", {})
        select = status_prop.get("select")
        return select.get("name") if select else None

    def update_status(self, page_id: str, status: str) -> None:
        self._client().pages.update(
            page_id=page_id,
            properties={"Status": {"select": {"name": status}}},
        )

    def update_status_failed(self, page_id: str, error: str) -> None:
        self._client().pages.update(
            page_id=page_id,
            properties={
                "Status": {"select": {"name": "Failed"}},
                "Error": {"rich_text": [{"text": {"content": error[:1900]}}]},
            },
        )


class FetcherResource(dg.ConfigurableResource):
    pi_socks5_url: str
    impersonate_profile: str = "safari17_0"
    jina_floor_chars: int = 2000
    timeout_s: int = 30
    youtube_proxy_url: str = ""

    def fetch_for_type(self, url: str, *, content_type: str) -> FetchResult:
        """Dispatch to per-type fetcher. Defensive fallback to article cascade
        for unknown types — should only trigger when triage misclassifies."""
        if content_type == "YouTube":
            from .fetchers import youtube

            return youtube.fetch(url, proxy_url=self.youtube_proxy_url or None)
        if content_type == "arXiv":
            from .fetchers import arxiv as arxiv_fetcher

            return arxiv_fetcher.fetch(url)
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


class ExtractQueueStore(dg.ConfigurableResource):
    db_path: str = str(LOCAL_QUEUE_DB)

    def _path(self) -> Path:
        return Path(self.db_path)

    def ensure_schema(self) -> None:
        queue_db.create_schema(db_path=self._path())

    def upsert_fetched(
        self,
        *,
        notion_page_id: str,
        url: str,
        raw_content: str,
        fetch_tier: str,
        fetch_tier_log: list[dict[str, Any]],
        fetched_content_char_count: int,
        content_hash: str,
    ) -> None:
        queue_db.upsert_fetched(
            db_path=self._path(),
            notion_page_id=notion_page_id,
            url=url,
            raw_content=raw_content,
            fetch_tier=fetch_tier,
            fetch_tier_log=fetch_tier_log,
            fetched_content_char_count=fetched_content_char_count,
            content_hash=content_hash,
        )

    def update_extracted(
        self,
        *,
        notion_page_id: str,
        extraction: dict[str, Any],
        prompt_label: str,
        prompt_sha256: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
    ) -> None:
        queue_db.update_extracted(
            db_path=self._path(),
            notion_page_id=notion_page_id,
            extraction=extraction,
            prompt_label=prompt_label,
            prompt_sha256=prompt_sha256,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

    def mark_failed(self, *, notion_page_id: str, error_text: str, url: str | None = None) -> None:
        queue_db.mark_failed(
            db_path=self._path(),
            notion_page_id=notion_page_id,
            error_text=error_text,
            url=url,
        )

    def get_row(self, notion_page_id: str) -> dict[str, Any] | None:
        return queue_db.get_row(db_path=self._path(), notion_page_id=notion_page_id)

    def list_with_stale_extraction(self, min_age_minutes: int) -> list[dict[str, Any]]:
        return queue_db.list_with_stale_extraction(
            db_path=self._path(), min_age_minutes=min_age_minutes
        )


def build_resources() -> dict[str, dg.ConfigurableResource]:
    return {
        "notion": NotionResource(
            integration_token=dg.EnvVar("NOTION_INTEGRATION_TOKEN"),
            queue_db_id=dg.EnvVar("NOTION_QUEUE_DB_ID"),
            queue_data_source_id=dg.EnvVar("NOTION_QUEUE_DATA_SOURCE_ID"),
        ),
        "fetcher": FetcherResource(
            pi_socks5_url=dg.EnvVar("PI_SOCKS5_URL"),
            impersonate_profile=dg.EnvVar("EXTRACT_QUEUE_IMPERSONATE_PROFILE"),
            youtube_proxy_url=dg.EnvVar("YOUTUBE_PROXY_URL").get_value(""),
        ),
        "extractor": ExtractorRegistry(
            openai_api_key=dg.EnvVar("OPENAI_API_KEY"),
            model=dg.EnvVar("EXTRACT_QUEUE_MODEL"),
            prompt_label_article=dg.EnvVar("EXTRACT_QUEUE_PROMPT_LABEL_ARTICLE"),
            prompt_label_youtube=dg.EnvVar("EXTRACT_QUEUE_PROMPT_LABEL_YOUTUBE"),
            prompt_label_arxiv=dg.EnvVar("EXTRACT_QUEUE_PROMPT_LABEL_ARXIV"),
        ),
        "store": ExtractQueueStore(),
    }
