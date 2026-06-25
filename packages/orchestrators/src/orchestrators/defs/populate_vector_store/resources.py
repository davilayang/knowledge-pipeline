# Resources for the populate_vector_store pipeline.

from pathlib import Path

import dagster as dg
from domains.notes.sources import LocalFileSource
from domains.raw_store.sources import RawStoreSource
from domains.sessions.sources import SessionsSource


class SourcesResource(dg.ConfigurableResource):
    """Three IngestSource instances rooted at ``backup_source_dir``."""

    backup_source_dir: str

    def raw_store(self) -> RawStoreSource:
        return RawStoreSource(Path(self.backup_source_dir) / "raw_store.db")

    def notes(self) -> LocalFileSource:
        return LocalFileSource(Path(self.backup_source_dir) / "notes")

    def sessions(self) -> SessionsSource:
        return SessionsSource(Path(self.backup_source_dir) / "sessions.db")
