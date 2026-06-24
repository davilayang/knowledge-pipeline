# Resources for the sync_wiki_curation pipeline — the ONLY place Notion "Wiki
# Pages" I/O lives (synthesis reads the local rejected_entities table; Notion is
# a review surface synced here). WikiPagesNotionResource carries both the
# curator-read (query_rejected, consumed by the PULL) and the producer-write
# (list_pages / upsert_page, consumed by the PUSH) sides.

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import dagster as dg
from domains.wiki.identity import normalize_name
from notion_client import Client as NotionClient
from pydantic import PrivateAttr


def _is_rate_limited(exc: Exception) -> bool:
    """True for a Notion 429 (rate-limit) error — duck-typed so we don't couple
    to notion_client's exception class hierarchy."""
    return getattr(exc, "status", None) == 429 or getattr(exc, "code", None) == "rate_limited"


def _retry_after_seconds(exc: Exception, attempt: int) -> float:
    """Honour Notion's Retry-After header if present, else exponential backoff."""
    headers = getattr(exc, "headers", None) or {}
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return float(2**attempt)


@dataclass(frozen=True)
class NotionPageRef:
    """A handle to an existing "Wiki Pages" row — the page_id to update, the
    Entity ID the push keys on (= wiki.db's surrogate entity_id), and the
    current Page status so the push can skip a row that's already orphaned."""

    page_id: str
    entity_id: str
    page_status: str


def _plain_text(prop: dict[str, Any]) -> str:
    """Join a Notion rich_text/title property's plain_text segments."""
    segments = prop.get("rich_text") or prop.get("title") or []
    return "".join(seg.get("plain_text", "") for seg in segments).strip()


def _select_name(prop: dict[str, Any]) -> str | None:
    sel = prop.get("select")
    return sel.get("name") if sel else None


class WikiPagesNotionResource(dg.ConfigurableResource):
    """Read + write access to the "Wiki Pages" Notion DB (the curation surface).

    query_rejected() returns the curator-marked rejection list keyed on the
    NORMALISED page Title (the entity's canonical name): {normalized_name:
    {category, reason}} for every row with Rejected=true. The surrogate Entity
    ID is minted post-extraction, so the denylist can't anchor on it — it
    matches on the name the curator sees instead. Reuses the shared
    NOTION_INTEGRATION_TOKEN; the data source id is the "Wiki Pages"
    collection.

    Producer columns (Title / Entity ID / Summary / Aliases / Source count /
    Page type / Last updated / Page status) are written by the PUSH; the curator
    columns (Rejected / Reject category / Reject reason) are NEVER written here —
    that column-ownership split is what prevents human/sync write-write conflicts.
    """

    integration_token: str
    wiki_pages_data_source_id: str
    # Notion allows ~3 req/s per integration; throttle proactively to stay under
    # it, and retry on the occasional 429 (honouring Retry-After). The push of
    # ~150 rows would otherwise blow the limit mid-run. Default 0 (no throttle)
    # keeps unit tests fast; production wiring (build_resources) sets the live
    # interval. The 429 retry is always on.
    min_request_interval_s: float = 0.0
    max_retries: int = 6

    _last_request_at: float = PrivateAttr(default=0.0)

    def _client(self) -> NotionClient:
        return NotionClient(auth=self.integration_token)

    def _request(self, fn: Callable[..., Any], **kwargs: Any) -> Any:
        """Call a Notion client method with proactive throttle + 429 retry."""
        for attempt in range(self.max_retries + 1):
            wait = self.min_request_interval_s - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            try:
                result = fn(**kwargs)
            except Exception as exc:  # noqa: BLE001 — re-raised unless it's a 429
                if not _is_rate_limited(exc) or attempt == self.max_retries:
                    raise
                time.sleep(_retry_after_seconds(exc, attempt))
                continue
            finally:
                self._last_request_at = time.monotonic()
            return result
        raise RuntimeError("unreachable")  # loop returns or raises

    def query_rejected(self) -> dict[str, dict[str, str | None]]:
        """All Rejected=true rows → {normalized_title: {category, reason}}.

        Scans the data source once, paginated via has_more/next_cursor. Rows
        with a blank Title are skipped (nothing to match on). The key is
        normalize_name(Title) so it lines up with both the extracted candidate
        name and the resolved entity's normalized_name at synthesis time.
        """
        client = self._client()
        out: dict[str, dict[str, str | None]] = {}
        cursor: str | None = None
        while True:
            kwargs: dict[str, Any] = {
                "data_source_id": self.wiki_pages_data_source_id,
                "filter": {"property": "Rejected", "checkbox": {"equals": True}},
                "page_size": 100,
            }
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = self._request(client.data_sources.query, **kwargs)
            for row in resp.get("results", []):
                props = row.get("properties", {})
                title = _plain_text(props.get("Title", {}))
                if not title:
                    continue
                out[normalize_name(title)] = {
                    "category": _select_name(props.get("Reject category", {})),
                    "reason": _plain_text(props.get("Reject reason", {})) or None,
                }
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
            if not cursor:
                break
        return out

    def list_pages(self) -> list[NotionPageRef]:
        """Every existing row → NotionPageRef, scanned once (paginated via
        has_more/next_cursor). No Rejected filter — the push needs the full set
        to decide create-vs-update. Archived (trashed) rows are skipped so the
        push never resurrects a page the curator deleted."""
        client = self._client()
        out: list[NotionPageRef] = []
        cursor: str | None = None
        while True:
            kwargs: dict[str, Any] = {
                "data_source_id": self.wiki_pages_data_source_id,
                "page_size": 100,
            }
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = self._request(client.data_sources.query, **kwargs)
            for row in resp.get("results", []):
                if row.get("archived") or row.get("in_trash"):
                    continue
                props = row.get("properties", {})
                out.append(
                    NotionPageRef(
                        page_id=row["id"],
                        entity_id=_plain_text(props.get("Entity ID", {})),
                        page_status=_select_name(props.get("Page status", {})) or "",
                    )
                )
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
            if not cursor:
                break
        return out

    def fetch_property_names(self) -> set[str]:
        """The data source's current property names — read once per push so a
        renamed/removed producer column fails the run loudly instead of silently
        writing garbage."""
        schema = self._request(
            self._client().data_sources.retrieve,
            data_source_id=self.wiki_pages_data_source_id,
        )
        return set(schema.get("properties", {}))

    def upsert_page(self, *, properties: dict[str, Any], page_id: str | None = None) -> str:
        """Create (page_id=None) or update a single row's properties; return the
        page_id. Writes ONLY the producer columns the caller passes — the
        curator columns (Rejected / Reject *) are never in `properties`, which
        is what keeps the human's edits and the sync from clobbering each
        other."""
        client = self._client()
        if page_id is None:
            resp = self._request(
                client.pages.create,
                parent={"type": "data_source_id", "data_source_id": self.wiki_pages_data_source_id},
                properties=properties,
            )
        else:
            resp = self._request(client.pages.update, page_id=page_id, properties=properties)
        return resp["id"]


def build_resources() -> dict[str, dg.ConfigurableResource]:
    return {
        "wiki_pages_notion": WikiPagesNotionResource(
            integration_token=dg.EnvVar("NOTION_INTEGRATION_TOKEN"),
            wiki_pages_data_source_id=dg.EnvVar("NOTION_WIKI_PAGES_DATA_SOURCE_ID"),
            # Stay under Notion's ~3 req/s during the ~150-row push.
            min_request_interval_s=0.34,
        ),
    }


__all__ = ["NotionPageRef", "WikiPagesNotionResource", "build_resources"]
