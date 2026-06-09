"""Shared resources for the Notion Knowledge OS Queue + local queue.db.

Both `triage_queued_items` and `extract_complex_contents` operate on the
same Notion Queue data source and the same `queue_items` SQLite table; the
resources below consolidate their access so neither pipeline owns the
class definition.

- `NotionQueueResource` — reads + writes against the Notion Queue. Separate
  query methods per pipeline (different filter shape) but the lifecycle
  writes (`write_triaged`, `update_status`, `update_status_failed`) are
  shared.
- `QueueStoreResource` — thin wrapper over `domains.queue_store.sources` covering
  both pipelines' write paths (`upsert_triaged`, `upsert_fetched`,
  `update_extracted`, `mark_failed`) and read helpers.
"""

from pathlib import Path
from typing import Any

import dagster as dg
from domains.queue_store import sources as queue_db
from notion_client import Client as NotionClient

from orchestrators.config import LOCAL_QUEUE_DB

_NOTION_ERROR_RICH_TEXT_CAP = 1900


def _build_rich_text(segments: list[tuple[str, str | None]]) -> list[dict[str, Any]]:
    """Convert (text, link_url | None) tuples into Notion rich_text array.
    Total content length is capped at `_NOTION_ERROR_RICH_TEXT_CAP` to stay
    well under Notion's 2000-char per-block limit."""
    out: list[dict[str, Any]] = []
    remaining = _NOTION_ERROR_RICH_TEXT_CAP
    for text, link in segments:
        chunk = text[:remaining]
        if not chunk:
            break
        node: dict[str, Any] = {"text": {"content": chunk}}
        if link:
            node["text"]["link"] = {"url": link}
        out.append(node)
        remaining -= len(chunk)
    return out


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
        """Status=Queued. Native status defaults to Queued on row creation,
        so mobile-share captures land here automatically — no empty branch
        needed."""
        resp = self._client().data_sources.query(
            data_source_id=self.queue_data_source_id,
            filter={"property": "Status", "status": {"equals": "Queued"}},
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
                    {"property": "Status", "status": {"equals": "Fetching"}},
                    type_clause,
                ]
            },
            page_size=page_size,
        )
        return list(resp.get("results", []))

    def get_status(self, page_id: str) -> str | None:
        page = self._client().pages.retrieve(page_id=page_id)
        status_prop = page.get("properties", {}).get("Status", {})
        status = status_prop.get("status")
        return status.get("name") if status else None

    # -------- writes --------

    def write_triaged(
        self,
        *,
        page_id: str,
        content_type: str,
        canonical_url: str,
        status_after: str,  # "Ready" (Tier B) or "Fetching" (Tier A)
        name: str | None = None,
        description: str | None = None,
        added_at_iso: str | None = None,
    ) -> None:
        """Two-step write: everything-non-Status first, then Status as the
        monotonic last write. If Status flipped first and the prior write then
        failed, the extract sensor would pick the row up without classification
        persisted to Notion.

        `name` and `description` are optional Notion-enrichment fields. `name`
        is the page Title; pass it only when Notion's existing Name is empty.
        `description` is a rich_text blurb; safe to always overwrite.
        `added_at_iso` backfills the Added At date when the row landed without
        one (mobile capture surfaces frequently omit it); pass None to leave
        Notion's Added At untouched.

        Notion-side `Canonical URL` is a text property (not a URL property)
        on purpose — Web Clipper / Save-to-Notion auto-pick a URL-typed
        property when the user hasn't explicitly mapped one, and we want the
        page URL to always land in the canonical `URL` field. Keeping
        Canonical URL as text leaves only one URL-type property on the
        Queue DB, removing the ambiguity entirely."""
        properties: dict[str, dict] = {
            "Content Type": {"select": {"name": content_type}},
            "Canonical URL": {"rich_text": [{"text": {"content": canonical_url}}]},
        }
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
        if added_at_iso:
            properties["Added At"] = {"date": {"start": added_at_iso}}
        client = self._client()
        client.pages.update(page_id=page_id, properties=properties)
        client.pages.update(
            page_id=page_id,
            properties={"Status": {"status": {"name": status_after}}},
        )

    def update_status(
        self,
        page_id: str,
        status: str,
        *,
        description: str | None = None,
    ) -> None:
        """Lifecycle Status flip used by extract `published`. Optional
        `description` overwrites Notion's Description property in the same
        call — kept optional so the failure-handling path can call this
        without touching Description. Strips both ends; an empty string after
        strip is treated as "don't write" so a no-op extraction never blanks
        a description previously seeded by triage."""
        properties: dict[str, dict] = {"Status": {"status": {"name": status}}}
        if description is not None:
            clean = description.strip()
            if clean:
                properties["Description"] = {"rich_text": [{"text": {"content": clean}}]}
        self._client().pages.update(page_id=page_id, properties=properties)

    def update_status_failed(self, page_id: str, error: str) -> None:
        self._client().pages.update(
            page_id=page_id,
            properties={
                "Status": {"status": {"name": "Failed"}},
                "Error": {"rich_text": [{"text": {"content": error[:1900]}}]},
            },
        )

    def update_status_skipped(self, page_id: str, segments: list[tuple[str, str | None]]) -> None:
        """Used by triage when a duplicate canonical_url is detected. Distinct
        from Failed so the Notion view can separate intentional skips (the
        system did the right thing) from real errors (the run blew up). The
        Notion Status SELECT must have a `Skipped` option before this is
        called; absence will raise a Notion API validation error.

        `segments` is a list of `(text, link_url | None)` tuples — the Error
        field is built as Notion rich_text with hyperlinks where link_url is
        non-None. Caller composes the message so the Notion-side reader can
        click through to the duplicated page / URL instead of staring at a
        bare UUID."""
        self._client().pages.update(
            page_id=page_id,
            properties={
                "Status": {"status": {"name": "Skipped"}},
                "Error": {"rich_text": _build_rich_text(segments)},
            },
        )

    def get_page_name(self, page_id: str) -> str | None:
        """Return the `Name` (title) property of a page, or None if empty /
        missing. Used by triage's duplicate-detection path to label the
        hyperlink back to the original row with something more useful than
        a UUID."""
        page = self._client().pages.retrieve(page_id=page_id)
        title = (page.get("properties", {}).get("Name", {}) or {}).get("title", []) or []
        text = "".join(t.get("plain_text", "") for t in title).strip()
        return text or None


class QueueStoreResource(dg.ConfigurableResource):
    """Thin wrapper over domains.queue_store.sources covering both pipelines.

    Triage owns `upsert_triaged` (page_id + url + canonical + content_type).
    Extract owns `upsert_fetched` (raw_content + provenance) and
    `record_extraction_calls` (one row per LLM call into extraction_calls
    + cohort summary update on queue_items).
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

    def find_canonical_url_duplicate(
        self, *, canonical_url: str, excluding_page_id: str
    ) -> str | None:
        return queue_db.find_canonical_url_duplicate(
            db_path=self._path(),
            canonical_url=canonical_url,
            excluding_page_id=excluding_page_id,
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

    def record_extraction_calls(
        self,
        *,
        notion_page_id: str,
        extractor_label: str,
        extractor_sha256: str,
        model: str,
        calls: list,
        tokens_in_total: int,
        tokens_out_total: int,
        langfuse_trace_id: str | None = None,
    ) -> None:
        """Three-call writer. Inserts one row per call into extraction_calls
        and updates queue_items cohort fields in one transaction."""
        queue_db.record_extraction_calls(
            db_path=self._path(),
            notion_page_id=notion_page_id,
            extractor_label=extractor_label,
            extractor_sha256=extractor_sha256,
            model=model,
            calls=calls,
            tokens_in_total=tokens_in_total,
            tokens_out_total=tokens_out_total,
            langfuse_trace_id=langfuse_trace_id,
        )

    def get_latest_extraction_calls(self, notion_page_id: str) -> dict[str, dict[str, Any]]:
        return queue_db.get_latest_extraction_calls(
            db_path=self._path(), notion_page_id=notion_page_id
        )

    def get_latest_topic_card(self, notion_page_id: str):
        """Convenience for the `published` asset — returns the latest TopicCard
        pydantic model parsed from extraction_calls, or None when absent."""
        from domains.extraction.schemas import TopicCard

        rows = self.get_latest_extraction_calls(notion_page_id)
        topic_row = rows.get("topic_card")
        if not topic_row or not topic_row.get("output"):
            return None
        return TopicCard.model_validate_json(topic_row["output"])

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

    def checkpoint_wal(self) -> None:
        """Fold the -wal sidecar back into the main queue.db file. Called
        after each extracted asset materialization so NA's read path
        (which opens with `immutable=1` and ignores WAL sidecars) sees
        fresh writes without waiting for SQLite's auto-checkpoint."""
        queue_db.checkpoint_wal(db_path=self._path())
