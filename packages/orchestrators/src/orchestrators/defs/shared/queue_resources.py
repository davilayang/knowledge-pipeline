"""Shared resources for the Notion Knowledge OS Queue + local queue.db.

Both `triage_queued_items` and `extract_complex_contents` operate on the
same Notion Queue data source and the same `queue_items` SQLite table; the
resources below consolidate their access so neither pipeline owns the
class definition.

- `NotionQueueResource` — reads + writes against the Notion Queue. Separate
  query methods per pipeline (different filter shape) but the lifecycle
  writes (`write_triaged`, `update_status`, `update_status_failed`) are
  shared.
- `QueueStoreResource` — thin wrapper over `domains.raw_store.queue` covering
  both pipelines' write paths (`upsert_triaged`, `upsert_fetched`,
  `update_extracted`, `mark_failed`) and read helpers.
"""

from pathlib import Path
from typing import Any

import dagster as dg
from domains.raw_store import queue as queue_db
from notion_client import Client as NotionClient

from orchestrators.config import LOCAL_QUEUE_DB


class NotionQueueResource(dg.ConfigurableResource):
    """Notion Queue DB access. Used by triage and extract pipelines.

    Triage writes Content Type, optional Name + Description, then Status as
    the monotonic last call. Extract reads Status=Fetching rows and writes
    Status=Ready / Failed on lifecycle transitions.
    """

    integration_token: str
    queue_db_id: str
    queue_data_source_id: str

    def _client(self) -> NotionClient:
        return NotionClient(auth=self.integration_token)

    # -------- reads --------

    def query_for_triage(self, *, page_size: int) -> list[dict[str, Any]]:
        """Status=Queued OR Status is empty. Empty absorbs mobile-share
        captures that bypass the Notion template (only Name + URL populated)."""
        resp = self._client().data_sources.query(
            data_source_id=self.queue_data_source_id,
            filter={
                "or": [
                    {"property": "Status", "select": {"equals": "Queued"}},
                    {"property": "Status", "select": {"is_empty": True}},
                ]
            },
            page_size=page_size,
        )
        return list(resp.get("results", []))

    def query_for_extract(
        self,
        *,
        page_size: int,
        supported_content_types: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        """Status=Fetching AND Content Type ∈ supported_content_types. Triage
        flips Status to Fetching after classification; this picks up only the
        types the extract pipeline knows how to fetch."""
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

    # -------- writes --------

    def write_triaged(
        self,
        *,
        page_id: str,
        content_type: str,
        status_after: str,  # "Ready" (Tier B) or "Fetching" (Tier A)
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        """Two-step write: everything-non-Status first, then Status as the
        monotonic last write. If Status flipped first and the prior write then
        failed, the extract sensor would pick the row up without classification
        persisted to Notion.

        `name` and `description` are optional Notion-enrichment fields. `name`
        is the page Title; pass it only when Notion's existing Name is empty.
        `description` is a rich_text blurb; safe to always overwrite."""
        properties: dict[str, dict] = {"Content Type": {"select": {"name": content_type}}}
        # Strip both ends — incoming title/description may include trailing
        # newlines from HTML metadata or padding from upstream writers. A value
        # that strips to empty is treated as "don't write" (don't blank a
        # user-set Name; don't write an empty Description property).
        clean_name = name.strip() if name is not None else None
        clean_description = description.strip() if description is not None else None
        if clean_name:
            properties["Name"] = {"title": [{"text": {"content": clean_name}}]}
        if clean_description:
            properties["Description"] = {"rich_text": [{"text": {"content": clean_description}}]}
        client = self._client()
        client.pages.update(page_id=page_id, properties=properties)
        client.pages.update(
            page_id=page_id,
            properties={"Status": {"select": {"name": status_after}}},
        )

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


class QueueStoreResource(dg.ConfigurableResource):
    """Thin wrapper over domains.raw_store.queue covering both pipelines.

    Triage owns `upsert_triaged` (page_id + url + canonical + content_type).
    Extract owns `upsert_fetched` (raw_content + provenance) and
    `update_extracted` (Topic Card payload + LLM provenance).
    """

    db_path: str = str(LOCAL_QUEUE_DB)

    def _path(self) -> Path:
        return Path(self.db_path)

    def ensure_schema(self) -> None:
        queue_db.create_schema(db_path=self._path())

    def upsert_triaged(
        self,
        *,
        notion_page_id: str,
        url: str,
        canonical_url: str,
        content_type: str,
    ) -> None:
        queue_db.upsert_triaged(
            db_path=self._path(),
            notion_page_id=notion_page_id,
            url=url,
            canonical_url=canonical_url,
            content_type=content_type,
        )

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
