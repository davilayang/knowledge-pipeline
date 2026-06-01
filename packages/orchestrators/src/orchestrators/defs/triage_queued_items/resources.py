"""Resources for the triage_queued_items pipeline."""

import re
from pathlib import Path
from typing import Any

import dagster as dg
import requests
from domains.raw_store import queue as queue_db
from notion_client import Client as NotionClient

from orchestrators.config import LOCAL_QUEUE_DB

from .def_config import TITLE_FETCH_TIMEOUT_S


class TriageNotionResource(dg.ConfigurableResource):
    """Notion reads + writes used only by triage.

    Reads the Queue data source for rows that need classification
    (Status=Queued OR Status is empty — the latter absorbs mobile-share
    template bypass).
    Writes content_type + optional Name + flipped Status back to the row.
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
        name_if_empty: str | None = None,
        status_after: str,  # "Ready" (Tier B) or "Fetching" (Tier A)
    ) -> None:
        """Execute Two-step write on a row:
        Write metadata fields first, then Status as the monotonic last write.

        If Status flipped first and the metadata write then failed, a subsequent
        sensor tick would re-pick the row up at the new Status
        without classification persisted to Notion.
        """

        props: dict = {"Content Type": {"select": {"name": content_type}}}
        if name_if_empty:
            props["Name"] = {"title": [{"text": {"content": name_if_empty[:200]}}]}

        client = self._client()
        client.pages.update(page_id=page_id, properties=props)
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


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class TitleFetcherResource(dg.ConfigurableResource):
    """Cheap GET for the page <title> tag when Notion's Name is empty.
    Best-effort: returns None on any error so the triage path doesn't fail
    on a Cloudflare block."""

    timeout_s: int = TITLE_FETCH_TIMEOUT_S

    def fetch_title(self, url: str) -> str | None:
        try:
            resp = requests.get(
                url,
                timeout=self.timeout_s,
                headers={"User-Agent": "kp-triage/1.0"},
            )
        except Exception:
            return None
        if resp.status_code != 200:
            return None
        match = _TITLE_RE.search(resp.text)
        if not match:
            return None
        title = match.group(1).strip()
        return title or None


def build_resources() -> dict[str, dg.ConfigurableResource]:
    return {
        "triage_notion": TriageNotionResource(
            integration_token=dg.EnvVar("NOTION_INTEGRATION_TOKEN"),
            queue_db_id=dg.EnvVar("NOTION_QUEUE_DB_ID"),
            queue_data_source_id=dg.EnvVar("NOTION_QUEUE_DATA_SOURCE_ID"),
        ),
        "triage_store": TriageQueueStore(),
        "title_fetcher": TitleFetcherResource(),
    }
