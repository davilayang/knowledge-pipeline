"""Resources for the extract_complex_contents pipeline.

- FetcherResource — HTTP client for the standalone `fetcher` service.
- ExtractorRegistry — strategy registry: content_type → ExtractorProtocol.

Notion + queue.db wrappers live in `orchestrators.defs.shared.queue_resources`
(shared with triage); `build_resources` binds them to local keys.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import dagster as dg
import httpx
from workflows.extraction import ThreeCallOpenAIExtractor

from orchestrators.defs.shared.queue_resources import (
    NotionQueueResource,
    QueueStoreResource,
)

from .def_config import (
    PROMPT_LABEL_FOLLOWUPS,
    PROMPT_LABEL_NARRATIVE,
    PROMPT_LABEL_TOPIC_CARD,
)

# Resolve repo-root prompts/extraction/ for the extractor registry.
# Anchor: this file lives at
#   packages/orchestrators/src/orchestrators/defs/extract_complex_contents/resources.py
#         parents[0] = extract_complex_contents/
#         parents[1] = defs/
#         parents[2] = orchestrators/         (package src)
#         parents[3] = src/
#         parents[4] = orchestrators/         (package root)
#         parents[5] = packages/
#         parents[6] = repo root
# Override with KP_PROMPTS_ROOT env var if set (used by evals + tests).
_DEFAULT_PROMPTS_ROOT = Path(__file__).resolve().parents[6] / "prompts"
_PROMPTS_ROOT = Path(os.environ.get("KP_PROMPTS_ROOT", _DEFAULT_PROMPTS_ROOT))
_PROMPTS_DIR = _PROMPTS_ROOT / "extraction"


@dataclass
class FetchResult:
    content: str = ""
    tier: str = ""
    tier_log: list[dict[str, Any]] = field(default_factory=list)
    title: str = ""
    error: str = ""
    transient: bool = False
    extras: dict[str, Any] = field(default_factory=dict)


class FetcherResource(dg.ConfigurableResource):
    service_url: str
    timeout_s: int = 60
    allow_paid: bool = True

    def fetch_for_type(self, url: str, *, content_type: str) -> FetchResult:
        del content_type  # service matches source by URL
        with httpx.Client(timeout=self.timeout_s) as client:
            return _call_service(client, self.service_url, url, allow_paid=self.allow_paid)

    def fetch(self, url: str) -> FetchResult:
        return self.fetch_for_type(url, content_type="Article")


def _call_service(
    client: httpx.Client,
    service_url: str,
    url: str,
    *,
    allow_paid: bool,
) -> FetchResult:
    endpoint = f"{service_url.rstrip('/')}/v1/fetch"
    payload = {
        "url": url,
        "quality": "fast",
        "allow_paid": allow_paid,
        "force_refresh": False,
    }
    try:
        resp = client.post(endpoint, json=payload)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        return FetchResult(error=f"fetcher service unreachable: {exc!r}", transient=True)
    except httpx.ReadTimeout as exc:
        return FetchResult(error=f"fetcher request timeout: {exc!r}", transient=True)
    except httpx.TransportError as exc:
        # Catches RemoteProtocolError, WriteTimeout, PoolTimeout, NetworkError —
        # the realistic "fetcher process restarted mid-request" shape.
        return FetchResult(error=f"fetcher transport error: {exc!r}", transient=True)

    if resp.status_code == 200:
        return _parse_success(resp)
    return _parse_problem(resp)


def _parse_success(resp: httpx.Response) -> FetchResult:
    try:
        body = resp.json()
    except ValueError:
        return FetchResult(
            error=f"malformed fetcher response: status=200 body={resp.text[:200]!r}",
            transient=False,
        )
    if "markdown" not in body:
        return FetchResult(
            error="malformed FetchOutcome: missing markdown",
            transient=False,
        )
    metadata = body.get("metadata") or {}
    return FetchResult(
        content=body["markdown"],
        tier=body.get("tier_used", ""),
        tier_log=body.get("tier_log") or [],
        title=str(metadata.get("title", "")),
        extras=metadata,
    )


def _parse_problem(resp: httpx.Response) -> FetchResult:
    try:
        problem = resp.json()
    except ValueError:
        return FetchResult(
            error=f"malformed fetcher response: status={resp.status_code} body={resp.text[:200]!r}",
            transient=False,
        )
    title = problem.get("title") or problem.get("code") or f"HTTP {resp.status_code}"
    detail = problem.get("detail")
    error = f"{title}: {detail}" if detail else title
    return FetchResult(
        error=error,
        transient=bool(problem.get("retryable", False)),
        tier_log=problem.get("tier_log") or [],
    )


class ExtractorRegistry(dg.ConfigurableResource):
    """Strategy registry — factory for the active ExtractorProtocol impl.

    v2: `ThreeCallOpenAIExtractor` is the only strategy; content-type
    routing now happens **inside** the prompts (each prompt's body branches
    on the `[content_type: ...]` tag the extractor prepends). Future LangGraph
    swap is a one-class change: change `build()` to return `LangGraphExtractor`
    instead — the asset code and storage shape don't move.

    Active prompt labels live as code constants in `def_config.py`
    (`PROMPT_LABEL_NARRATIVE` etc.) — not env vars. Prompt versions don't
    vary per-deployment, so dev/prod env symmetry doesn't justify wiring
    them through Dagster Config; bumping a prompt means editing the
    markdown file AND the constant in the same commit, no env drift.

    Callers must `build()` ONCE per asset run and reuse the returned
    extractor for `extract()` + reads of `bundle_label` / `bundle_sha256` /
    `model`. Each build constructs a fresh AsyncOpenAI client that's closed
    at the end of `extract()` (see ThreeCallOpenAIExtractor docstring) —
    calling `build()` more than once per run wastes httpx pools."""

    openai_api_key: str
    model: str
    max_tokens: int = 2048

    def _prompt_text(self, label: str) -> str:
        return (_PROMPTS_DIR / f"{label}.md").read_text()

    def build(self) -> ThreeCallOpenAIExtractor:
        return ThreeCallOpenAIExtractor(
            api_key=self.openai_api_key,
            model=self.model,
            narrative_prompt=self._prompt_text(PROMPT_LABEL_NARRATIVE),
            narrative_prompt_label=PROMPT_LABEL_NARRATIVE,
            topic_card_prompt=self._prompt_text(PROMPT_LABEL_TOPIC_CARD),
            topic_card_prompt_label=PROMPT_LABEL_TOPIC_CARD,
            followups_prompt=self._prompt_text(PROMPT_LABEL_FOLLOWUPS),
            followups_prompt_label=PROMPT_LABEL_FOLLOWUPS,
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
            service_url=dg.EnvVar("FETCHER_URL"),
            timeout_s=int(dg.EnvVar("FETCHER_TIMEOUT_S").get_value("60") or "60"),
            allow_paid=(dg.EnvVar("FETCHER_ALLOW_PAID").get_value("true") or "true").lower()
            == "true",
        ),
        "extractor": ExtractorRegistry(
            openai_api_key=dg.EnvVar("OPENAI_API_KEY"),
            model=dg.EnvVar("EXTRACT_QUEUE_MODEL"),
        ),
        "store": QueueStoreResource(),
    }
