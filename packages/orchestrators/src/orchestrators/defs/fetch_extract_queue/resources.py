"""Resources for the fetch_extract_queue pipeline.

- FetcherResource — HTTP client for the standalone `fetcher` service, which
  both fetches content and extracts from it.

Notion + queue.db wrappers live in `orchestrators.defs.shared.queue_resources`
(shared with triage); `build_resources` binds them to local keys.
"""

from dataclasses import dataclass, field
from typing import Any

import dagster as dg
import httpx

from orchestrators.defs.shared.queue_resources import (
    NotionQueueResource,
    QueueStoreResource,
)


@dataclass
class FetchResult:
    content: str = ""
    tier: str = ""
    tier_log: list[dict[str, Any]] = field(default_factory=list)
    title: str = ""
    error: str = ""
    transient: bool = False
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractResult:
    """One `/v1/extract` response, indexed the way the assets consume it.

    Task-level and request-level failures stay apart. `failures` names tasks the
    service ran and could not complete, and the caller decides what each one
    means — a missing reading card fails the item, a missing metadata row does
    not. `error` means the request never produced results at all, which is not
    any single task's fault.
    """

    payloads: dict[str, dict[str, Any]] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    cache_hits: list[str] = field(default_factory=list)
    error: str = ""
    transient: bool = False


@dataclass
class ExtractionSettings:
    """What `/v1/extract` would run with right now, read without running it."""

    model: str
    by_task: dict[str, dict[str, str]]


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

    def extract(
        self,
        content: str,
        *,
        content_type: str,
        tasks: list[str],
        user_notes: str | None = None,
        model: str | None = None,
    ) -> ExtractResult:
        """Run the named extraction tasks over `content` on the fetcher service."""
        endpoint = f"{self.service_url.rstrip('/')}/v1/extract"
        payload: dict[str, Any] = {
            "content": content,
            "content_type": content_type,
            "tasks": tasks,
        }
        if user_notes:
            payload["user_notes"] = user_notes
        if model:
            payload["model"] = model
        with self._client() as client:
            try:
                resp = client.post(endpoint, json=payload)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                return ExtractResult(error=f"fetcher service unreachable: {exc!r}", transient=True)
            except httpx.ReadTimeout as exc:
                return ExtractResult(error=f"fetcher request timeout: {exc!r}", transient=True)
            except httpx.TransportError as exc:
                return ExtractResult(error=f"fetcher transport error: {exc!r}", transient=True)

        if resp.status_code != 200:
            problem = _parse_problem(resp)
            return ExtractResult(error=problem.error, transient=problem.transient)

        try:
            body = resp.json()
        except ValueError:
            return ExtractResult(
                error=f"malformed extract response: body={resp.text[:200]!r}", transient=False
            )
        return ExtractResult(
            payloads={r["task"]: r["payload"] for r in body.get("results") or []},
            failures={
                e["task"]: f"{e.get('code', 'TASK_FAILED')}: {e.get('detail')}"
                for e in body.get("errors") or []
            },
            calls=body.get("calls") or [],
            cache_hits=body.get("cache_hits") or [],
        )

    def extraction_prompts(self) -> "ExtractionSettings":
        """What a run would use: the active model, and each task's prompt label
        and staleness sha.

        Read before deciding whether a stored extraction is current. The model is
        the service's to choose, and the sha covers the shared system message,
        the article envelope and the generated schema as well as the prompt file
        — none of which this repo can see once the implementation lives there.
        """
        endpoint = f"{self.service_url.rstrip('/')}/v1/extract/prompts"
        with self._client() as client:
            resp = client.get(endpoint)
        resp.raise_for_status()
        body = resp.json()
        return ExtractionSettings(
            model=body["model"],
            by_task={
                p["task"]: {"prompt_label": p["prompt_label"], "prompt_sha256": p["prompt_sha256"]}
                for p in body["prompts"]
            },
        )


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
        "store": QueueStoreResource(),
    }
