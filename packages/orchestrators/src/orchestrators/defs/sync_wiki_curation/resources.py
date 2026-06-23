# Resources for the sync_wiki_curation pipeline — the ONLY place Notion "Wiki
# Pages" I/O lives (synthesis reads the local rejected_entities table; Notion is
# a review surface synced here). WikiPagesNotionResource carries both the
# curator-read (query_rejected, consumed by the PULL) and the producer-write
# (list_pages / upsert_page, consumed by the PUSH) sides.

from dataclasses import dataclass
from typing import Any

import dagster as dg
from domains.wiki.identity import normalize_name
from notion_client import Client as NotionClient


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

    Producer columns (Title / Entity ID / Summary / Source count / Page type /
    Last updated / Page status) are written by the PUSH; the curator columns
    (Rejected / Reject category / Reject reason) are NEVER written here — that
    column-ownership split is what prevents human/sync write-write conflicts.
    """

    integration_token: str
    wiki_pages_data_source_id: str

    def _client(self) -> NotionClient:
        return NotionClient(auth=self.integration_token)

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
            resp = client.data_sources.query(**kwargs)
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
            resp = client.data_sources.query(**kwargs)
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
        schema = self._client().data_sources.retrieve(data_source_id=self.wiki_pages_data_source_id)
        return set(schema.get("properties", {}))

    def upsert_page(self, *, properties: dict[str, Any], page_id: str | None = None) -> str:
        """Create (page_id=None) or update a single row's properties; return the
        page_id. Writes ONLY the producer columns the caller passes — the
        curator columns (Rejected / Reject *) are never in `properties`, which
        is what keeps the human's edits and the sync from clobbering each
        other."""
        client = self._client()
        if page_id is None:
            resp = client.pages.create(
                parent={"type": "data_source_id", "data_source_id": self.wiki_pages_data_source_id},
                properties=properties,
            )
        else:
            resp = client.pages.update(page_id=page_id, properties=properties)
        return resp["id"]


def build_resources() -> dict[str, dg.ConfigurableResource]:
    return {
        "wiki_pages_notion": WikiPagesNotionResource(
            integration_token=dg.EnvVar("NOTION_INTEGRATION_TOKEN"),
            wiki_pages_data_source_id=dg.EnvVar("NOTION_WIKI_PAGES_DATA_SOURCE_ID"),
        ),
    }


__all__ = ["NotionPageRef", "WikiPagesNotionResource", "build_resources"]
