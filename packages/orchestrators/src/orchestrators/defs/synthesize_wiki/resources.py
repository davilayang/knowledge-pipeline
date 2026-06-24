# Resources for the synthesize_wiki pipeline.

from pathlib import Path
from typing import Any

import dagster as dg
from domains.wiki.identity import normalize_name
from domains.wiki.state import create_schema
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

    query_rejected() returns the curator-marked rejection list keyed on the
    NORMALISED page Title (the entity's canonical name): {normalized_name:
    {category, reason}} for every row with Rejected=true. The surrogate Entity
    ID is minted post-extraction, so the denylist can't anchor on it — it
    matches on the name the curator sees instead. Uses the per-database
    NOTION_WIKI_TOKEN (scoped to the "Wiki Pages" DB, read+write); the data
    source id is the "Wiki Pages" collection. NOT wired today — synthesis reads
    the denylist from the local rejected_entities table; this resource is for
    the forthcoming sync_wiki_curation DAG (read Rejected, write entities).
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


class WikiResource(dg.ConfigurableResource):
    """Paths for wiki synthesis.

    Reads the source raw_store from the backup_readings snapshot for the
    current partition (`backup_dir/<partition_key>/raw_store.db`). 1:1
    binding via IdentityPartitionMapping — if the snapshot is missing,
    wiki/pending raises rather than falling back to anything older.

    All durable state (the processed / pages / aliases / page_sources tables)
    lives in the SQLite file at `wiki_db_path`, alongside the other local
    stores under the host-mounted data dir (survives `down -v`). get_db_path()
    ensures the schema is applied (idempotent) before any asset touches it.
    """

    wiki_dir: str = str(DATA_DIR / "wiki")
    wiki_db_path: str = str(DATA_DIR / "wiki.db")
    backup_dir: str

    def get_wiki_dir(self) -> Path:
        return Path(self.wiki_dir)

    def get_db_path(self) -> Path:
        """Return the wiki.db path, ensuring the schema exists (idempotent)."""
        path = Path(self.wiki_db_path)
        create_schema(db_path=path)
        return path

    def get_backup_dir(self) -> Path:
        return Path(self.backup_dir)

    def snapshot_path_for(self, partition_key: str) -> Path:
        """Path to raw_store.db inside the snapshot dir for `partition_key`.

        Pure derivation; does not check that the file exists. Callers
        (wiki/pending) handle the missing-file case."""
        return self.get_backup_dir() / partition_key / "raw_store.db"


def build_resources() -> dict[str, dg.ConfigurableResource]:
    # WikiPagesNotionResource (query_rejected) is intentionally NOT wired here:
    # synthesis now reads the denylist from the local rejected_entities table, so
    # nothing in this pipeline consumes Notion. The class is retained for the
    # forthcoming sync_wiki_curation DAG, which will wire it in its own resources
    # (keeping the NOTION_* env vars off this code location until then).
    return {
        "wiki": WikiResource(
            backup_dir=dg.EnvVar("BACKUP_DST_DIR"),
        ),
    }
