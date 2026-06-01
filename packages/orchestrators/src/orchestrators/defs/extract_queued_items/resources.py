"""Resources for the extract_queued_items pipeline.

- NotionResource — lifecycle-only writes to the Knowledge OS Queue DB.
- FetcherResource — Jina then curl-cffi + Pi SOCKS5 cascade with trafilatura.
- ExtractorResource — Anthropic SDK; loads the kp-local prompt copy once.
- ExtractQueueStore — thin wrapper over domains.raw_store.queue.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import dagster as dg
from anthropic import Anthropic
from domains.raw_store import queue as queue_db
from notion_client import Client as NotionClient

from orchestrators.config import LOCAL_QUEUE_DB

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

_TOPIC_CARD_KEYS = (
    "extracted_title",
    "core_mechanism",
    "best_example",
    "second_example",
    "transferable_pattern",
    "main_tension",
    "candidate_tie_backs",
)


@dataclass
class FetchResult:
    content: str
    tier: str
    tier_log: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ExtractionUsage:
    input_tokens: int
    output_tokens: int


class NotionResource(dg.ConfigurableResource):
    integration_token: str
    queue_db_id: str
    queue_data_source_id: str

    def _client(self) -> NotionClient:
        return NotionClient(auth=self.integration_token)

    def query_queue(self, *, status: str, page_size: int) -> list[dict[str, Any]]:
        # notion-client 2.x moved query() from databases to data_sources —
        # Notion now models databases as containers for one or more data
        # sources, and queries run against a data source. queue_db_id is kept
        # for documentation + future write paths; the query path uses ds_id.
        #
        # The Status=empty branch absorbs the Notion free-tier limitation
        # that the mobile Share Sheet (and Web Clipper) bypasses database
        # templates — rows land with Name+URL filled and every other
        # property empty. fetched_content flips Status=Fetching on pickup,
        # so the empty-Status state is short-lived.
        resp = self._client().data_sources.query(
            data_source_id=self.queue_data_source_id,
            filter={
                "or": [
                    {"property": "Status", "select": {"equals": status}},
                    {"property": "Status", "select": {"is_empty": True}},
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

    def fetch(self, url: str) -> FetchResult:
        tier_log: list[dict[str, Any]] = []

        jina_content, jina_status, jina_error = _jina_fetch(url, timeout_s=self.timeout_s)
        tier_log.append(
            {
                "tier": "jina",
                "status": jina_status,
                "chars": len(jina_content),
                "error": jina_error,
            }
        )
        if jina_content and len(jina_content) >= self.jina_floor_chars:
            return FetchResult(content=jina_content, tier="jina", tier_log=tier_log)

        html, curl_status, curl_error = _curl_cffi_fetch(
            url,
            proxy=self.pi_socks5_url,
            impersonate=self.impersonate_profile,
            timeout_s=self.timeout_s,
        )
        markdown = _trafilatura_extract(html) if html else ""
        tier_log.append(
            {
                "tier": "curl_cffi",
                "status": curl_status,
                "chars": len(markdown),
                "error": curl_error,
            }
        )
        return FetchResult(content=markdown, tier="curl_cffi", tier_log=tier_log)


class ExtractorResource(dg.ConfigurableResource):
    anthropic_api_key: str
    model: str
    prompt_label: str
    max_tokens: int = 2048

    def _prompt_path(self) -> Path:
        return _PROMPTS_DIR / f"{self.prompt_label}.md"

    @property
    def prompt_text(self) -> str:
        return self._prompt_path().read_text()

    @property
    def prompt_sha256(self) -> str:
        return hashlib.sha256(self.prompt_text.encode()).hexdigest()

    def extract(self, content: str) -> tuple[dict[str, Any], ExtractionUsage]:
        client = Anthropic(api_key=self.anthropic_api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.prompt_text,
            messages=[{"role": "user", "content": content}],
        )
        body_text = "".join(getattr(b, "text", "") for b in response.content)
        extraction = _parse_topic_card(body_text)
        usage = ExtractionUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        return extraction, usage


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


def _jina_fetch(url: str, *, timeout_s: int) -> tuple[str, int | None, str | None]:
    """Pull markdown via r.jina.ai. Returns (content, http_status, error)."""
    import requests

    try:
        resp = requests.get(f"https://r.jina.ai/{url}", timeout=timeout_s)
        return resp.text or "", resp.status_code, None
    except Exception as exc:  # pragma: no cover — exercised via monkeypatch in tests
        return "", None, str(exc)


def _curl_cffi_fetch(
    url: str, *, proxy: str, impersonate: str, timeout_s: int
) -> tuple[str, int | None, str | None]:
    """Fetch through Pi SOCKS5 with browser-impersonating TLS fingerprint."""
    from curl_cffi import requests as curl_requests

    try:
        resp = curl_requests.get(
            url,
            impersonate=impersonate,
            proxies={"http": proxy, "https": proxy},
            timeout=timeout_s,
        )
        return resp.text or "", resp.status_code, None
    except Exception as exc:  # pragma: no cover — exercised via monkeypatch in tests
        return "", None, str(exc)


def _trafilatura_extract(html: str) -> str:
    import trafilatura

    return (
        trafilatura.extract(
            html,
            output_format="markdown",
            include_links=True,
            include_tables=True,
        )
        or ""
    )


def _parse_topic_card(text: str) -> dict[str, Any]:
    """Parse the JSON block emitted by the v5 extraction prompt.

    Maps the prompt's `title` field to our schema's `extracted_title`. Drops
    any extra keys; passes through known Topic Card fields. Raises ValueError
    if no JSON object can be located — the asset turns that into dg.Failure."""
    match = _JSON_BLOCK_RE.search(text)
    payload = match.group(1) if match else text.strip()
    data = json.loads(payload)
    if "title" in data and "extracted_title" not in data:
        data["extracted_title"] = data.pop("title")
    return {k: data.get(k) for k in _TOPIC_CARD_KEYS}


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
        ),
        "extractor": ExtractorResource(
            anthropic_api_key=dg.EnvVar("ANTHROPIC_API_KEY"),
            model=dg.EnvVar("EXTRACT_QUEUE_MODEL"),
            prompt_label=dg.EnvVar("EXTRACT_QUEUE_PROMPT_LABEL"),
        ),
        "store": ExtractQueueStore(),
    }
