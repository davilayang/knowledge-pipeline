"""Resources for the triage_queued_items pipeline."""

from pathlib import Path
from typing import Any

import dagster as dg
from domains.raw_store import queue as queue_db
from notion_client import Client as NotionClient

from orchestrators.config import LOCAL_QUEUE_DB


# TODO: move to shared/resources
class TriageNotionResource(dg.ConfigurableResource):
    """Notion reads + writes used only by triage.

    Reads the Queue data source for rows that need classification
    (Status=Queued OR Status is empty — the latter absorbs mobile-share
    template bypass).
    Writes Content Type, Status, and (optionally) Name + Description back to
    the row. Name is only written when the user left it blank — extract /
    NA can still overwrite it later from real content.
    """

    integration_token: str
    queue_db_id: str
    queue_data_source_id: str

    def _client(self) -> NotionClient:
        return NotionClient(auth=self.integration_token)

    def query_queue(self, *, page_size: int) -> list[dict[str, Any]]:
        """Return rows with Status=Queued OR Status is empty.

        NOTE: Mobile Share Sheet bypasses Notion templates so freshly-captured
        rows arrive with empty Status (only Name and URL are populated)
        """

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
        is the page Title; pass it only when Notion's existing Name is empty
        (caller decides — TriageInput.name passthrough). `description` is a
        rich_text blurb; safe to always overwrite."""
        properties: dict[str, dict] = {"Content Type": {"select": {"name": content_type}}}
        if name is not None:
            properties["Name"] = {"title": [{"text": {"content": name}}]}
        if description is not None:
            properties["Description"] = {"rich_text": [{"text": {"content": description}}]}
        client = self._client()
        client.pages.update(page_id=page_id, properties=properties)
        client.pages.update(
            page_id=page_id,
            properties={"Status": {"select": {"name": status_after}}},
        )

    def update_status_failed(self, page_id: str, error: str) -> None:
        """Write Status field if Failed to handle a row"""
        self._client().pages.update(
            page_id=page_id,
            properties={
                "Status": {"select": {"name": "Failed"}},
                "Error": {"rich_text": [{"text": {"content": error[:1900]}}]},
            },
        )


# TODO: move to shared/queue_store.py
class TriageQueueStore(dg.ConfigurableResource):
    """Thin wrapper around domains.raw_store.queue for the triage path."""

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


def build_resources() -> dict[str, dg.ConfigurableResource]:
    return {
        "triage_notion": TriageNotionResource(
            integration_token=dg.EnvVar("NOTION_INTEGRATION_TOKEN"),
            queue_db_id=dg.EnvVar("NOTION_QUEUE_DB_ID"),
            queue_data_source_id=dg.EnvVar("NOTION_QUEUE_DATA_SOURCE_ID"),
        ),
        "triage_store": TriageQueueStore(),
    }
