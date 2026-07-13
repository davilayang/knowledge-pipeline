"""Resources for the fetch_extract_queue pipeline.

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
from workflows.extraction import PromptBundle, ThreeCallOpenAIExtractor

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
#   packages/orchestrators/src/orchestrators/defs/fetch_extract_queue/resources.py
#         parents[0] = fetch_extract_queue/
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
    timeout_s: int
    # str-typed because Dagster has no `EnvVar.bool` — resolved to "true"/"false"
    # at run init, parsed at the call site below.
    allow_paid: str

    def _client(self) -> httpx.Client:
        # trust_env=False keeps the orchestrator → fetcher call off any
        # HTTP(S)_PROXY the user has set for outbound web fetches. Internal
        # service-to-service calls should never tunnel through an upstream
        # proxy — that proxy is for the fetcher's own egress, not for talking
        # to the fetcher.
        return httpx.Client(timeout=self.timeout_s, trust_env=False)

    def fetch_for_type(self, url: str, *, content_type: str) -> FetchResult:
        del content_type  # service matches source by URL
        allow_paid = self.allow_paid.lower() == "true"
        with self._client() as client:
            return _call_service(client, self.service_url, url, allow_paid=allow_paid)

    def fetch(self, url: str) -> FetchResult:
        # content_type is discarded by fetch_for_type (the service routes by URL);
        # pass the catch-all so no stale taxonomy value lingers here.
        return self.fetch_for_type(url, content_type="article")

    def structure(self, raw_content: str, *, title: str = "", source_url: str = "") -> FetchResult:
        endpoint = f"{self.service_url.rstrip('/')}/v1/structure"
        payload: dict[str, Any] = {"raw_content": raw_content}
        if title:
            payload["title"] = title
        if source_url:
            payload["source_url"] = source_url
        with self._client() as client:
            try:
                resp = client.post(endpoint, json=payload)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                return FetchResult(error=f"fetcher service unreachable: {exc!r}", transient=True)
            except httpx.ReadTimeout as exc:
                return FetchResult(error=f"fetcher request timeout: {exc!r}", transient=True)
            except httpx.TransportError as exc:
                return FetchResult(error=f"fetcher transport error: {exc!r}", transient=True)

        if resp.status_code == 200:
            return _parse_success(resp)
        return _parse_problem(resp)


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
        # One bundle registered as the generic fallback. Per-shape bundles
        # land alongside once Phase-6 lift evidence motivates them —
        # registering more shapes is a single-line addition here.
        generic_bundle = PromptBundle(
            narrative=(self._prompt_text(PROMPT_LABEL_NARRATIVE), PROMPT_LABEL_NARRATIVE),
            topic_card=(self._prompt_text(PROMPT_LABEL_TOPIC_CARD), PROMPT_LABEL_TOPIC_CARD),
            followups=(self._prompt_text(PROMPT_LABEL_FOLLOWUPS), PROMPT_LABEL_FOLLOWUPS),
        )
        return ThreeCallOpenAIExtractor(
            api_key=self.openai_api_key,
            model=self.model,
            prompt_sets={"unknown": generic_bundle},
            max_tokens=self.max_tokens,
        )


def build_resources() -> dict[str, dg.ConfigurableResource]:
    return {
        "notion": NotionQueueResource(
            integration_token=dg.EnvVar("NOTION_QUEUE_TOKEN"),
            queue_db_id=dg.EnvVar("NOTION_QUEUE_DB_ID"),
            queue_data_source_id=dg.EnvVar("NOTION_QUEUE_DATA_SOURCE_ID"),
        ),
        "fetcher": FetcherResource(
            service_url=dg.EnvVar("FETCHER_URL"),
            timeout_s=dg.EnvVar.int("FETCHER_TIMEOUT_S"),
            allow_paid=dg.EnvVar("FETCHER_ALLOW_PAID"),
        ),
        "extractor": ExtractorRegistry(
            openai_api_key=dg.EnvVar("OPENAI_API_KEY"),
            model=dg.EnvVar("EXTRACT_QUEUE_MODEL"),
        ),
        "store": QueueStoreResource(),
    }
