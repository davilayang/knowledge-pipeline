# Resources for the synthesize_wiki pipeline.

from pathlib import Path
from typing import Any

import dagster as dg
from notion_client import Client as NotionClient

from orchestrators.config import DATA_DIR


def _plain_text(prop: dict[str, Any]) -> str:
    """Join a Notion rich_text/title property's plain_text segments."""
    segments = prop.get("rich_text") or prop.get("title") or []
    return "".join(seg.get("plain_text", "") for seg in segments).strip()


def _select_name(prop: dict[str, Any]) -> str | None:
    sel = prop.get("select")
    return sel.get("name") if sel else None


class WikiPagesNotionResource(dg.ConfigurableResource):
    """Read access to the "Wiki Pages" Notion DB for the W2.5 denylist.

    query_rejected() returns the curator-marked rejection list:
    {entity_id: {category, reason}} for every row with Rejected=true. The
    synthesized asset filters these entity_ids out of synthesis. Reuses the
    shared NOTION_INTEGRATION_TOKEN; the data source id is the "Wiki Pages"
    collection. Read-only — never writes curator columns.
    """

    integration_token: str
    wiki_pages_data_source_id: str

    def _client(self) -> NotionClient:
        return NotionClient(auth=self.integration_token)

    def query_rejected(self) -> dict[str, dict[str, str | None]]:
        """All Rejected=true rows → {entity_id: {category, reason}}.

        Scans the data source once, paginated via has_more/next_cursor.
        Rows with a blank Entity ID are skipped (a curator added a row but
        hasn't set the key yet — can't match it deterministically anyway).
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
                entity_id = _plain_text(props.get("Entity ID", {}))
                if not entity_id:
                    continue
                out[entity_id] = {
                    "category": _select_name(props.get("Reject category", {})),
                    "reason": _plain_text(props.get("Reject reason", {})) or None,
                }
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
            if not cursor:
                break
        return out


class WikiResource(dg.ConfigurableResource):
    """Paths for wiki synthesis.

    Reads the source raw_store from the backup_readings snapshot for the
    current partition (`backup_dir/<partition_key>/raw_store.db`). 1:1
    binding via IdentityPartitionMapping — if the snapshot is missing,
    wiki/pending raises rather than falling back to anything older.

    All durable state (wiki.processed / wiki.pages / wiki.aliases plus the
    LangGraph checkpoints) lives in the Postgres pointed to by
    `database_url`. Resolved at run init via dg.EnvVar — an unset
    DATABASE_URL fails fast rather than leaking a cryptic psycopg error
    from inside the workflow.
    """

    wiki_dir: str = str(DATA_DIR / "wiki")
    backup_dir: str
    database_url: str

    def get_wiki_dir(self) -> Path:
        return Path(self.wiki_dir)

    def get_backup_dir(self) -> Path:
        return Path(self.backup_dir)

    def snapshot_path_for(self, partition_key: str) -> Path:
        """Path to raw_store.db inside the snapshot dir for `partition_key`.

        Pure derivation; does not check that the file exists. Callers
        (wiki/pending) handle the missing-file case."""
        return self.get_backup_dir() / partition_key / "raw_store.db"


def build_resources() -> dict[str, dg.ConfigurableResource]:
    return {
        "wiki": WikiResource(
            backup_dir=dg.EnvVar("BACKUP_DST_DIR"),
            database_url=dg.EnvVar("DATABASE_URL"),
        ),
        "wiki_pages_notion": WikiPagesNotionResource(
            integration_token=dg.EnvVar("NOTION_INTEGRATION_TOKEN"),
            wiki_pages_data_source_id=dg.EnvVar("NOTION_WIKI_PAGES_DATA_SOURCE_ID"),
        ),
    }
