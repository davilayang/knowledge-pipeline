# Resources for the synthesize_wiki pipeline.

from datetime import date
from pathlib import Path

import dagster as dg

from orchestrators.config import BACKUP_DIR, DATA_DIR


class WikiResource(dg.ConfigurableResource):
    """Paths for wiki synthesis.

    Reads the source raw_store from the most recent `backup_readings`
    snapshot under `backup_dir/<YYYY-MM-DD>/raw_store.db` rather than the
    live newsletter-assistant DB. Symmetric across laptop/server: both
    consume the daily snapshot, decoupling wiki cadence from upstream
    write activity.

    All durable state (wiki.processed / wiki.pages / wiki.aliases plus the
    LangGraph checkpoints) lives in the Postgres pointed to by
    `database_url`. Resolved at run init via dg.EnvVar — an unset
    DATABASE_URL fails fast rather than leaking a cryptic psycopg error
    from inside the workflow.
    """

    wiki_dir: str = str(DATA_DIR / "wiki")
    backup_dir: str = str(BACKUP_DIR)
    database_url: str

    def get_wiki_dir(self) -> Path:
        return Path(self.wiki_dir)

    def get_backup_dir(self) -> Path:
        return Path(self.backup_dir)

    def latest_raw_store_snapshot(self) -> tuple[Path, date] | None:
        """Newest <date>/raw_store.db under backup_dir, or None if absent.

        Scans for ISO-date subdirs (YYYY-MM-DD) that contain raw_store.db.
        Caller decides staleness.
        """
        root = self.get_backup_dir()
        if not root.is_dir():
            return None
        candidates: list[tuple[date, Path]] = []
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            try:
                d = date.fromisoformat(entry.name)
            except ValueError:
                continue
            db_path = entry / "raw_store.db"
            if db_path.exists():
                candidates.append((d, db_path))
        if not candidates:
            return None
        d, p = max(candidates, key=lambda t: t[0])
        return p, d


def build_resources() -> dict[str, dg.ConfigurableResource]:
    return {
        "wiki": WikiResource(database_url=dg.EnvVar("DATABASE_URL")),
    }
