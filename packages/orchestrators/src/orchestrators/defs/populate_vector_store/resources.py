# Resources for the populate_vector_store pipeline.

from pathlib import Path

import dagster as dg
from domains.notes.sources import LocalFileSource
from domains.raw_store.sources import RawStoreSource
from domains.sessions.sources import SessionsSource
from domains.wiki.sources import WikiSource

from orchestrators.config import LOCAL_WIKI_DIR


class SourcesResource(dg.ConfigurableResource):
    """IngestSource instances. Most root at ``backup_source_dir`` (the synced
    newsletter-assistant data); wiki is kp-owned and roots at this repo's
    ``LOCAL_WIKI_DIR`` instead — it is not part of the NA backup mount."""

    backup_source_dir: str

    def raw_store(self) -> RawStoreSource:
        # File is corpus.db since newsletter-assistant's 0.46.0 three-DB topology
        # rename; the lane keeps the name "raw_store" because it is written into
        # every stored chunk's source_ref and is the delete key on re-embed.
        return RawStoreSource(Path(self.backup_source_dir) / "corpus.db")

    def notes(self) -> LocalFileSource:
        return LocalFileSource(Path(self.backup_source_dir) / "notes")

    def briefs(self) -> LocalFileSource:
        return LocalFileSource(Path(self.backup_source_dir) / "briefs")

    def sessions(self) -> SessionsSource:
        return SessionsSource(Path(self.backup_source_dir) / "sessions.db")

    def wiki(self) -> WikiSource:
        return WikiSource(LOCAL_WIKI_DIR)
