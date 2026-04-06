# src/knowledge_pipeline/defs/shared/raw_store.py
# Shared asset that creates a copy pinned raw_store database for
# various chunking strategies

import hashlib
import sqlite3

import dagster as dg
from dagster import AssetExecutionContext

from knowledge_pipeline.config import DATA_DIR, SOURCE_RAW_STORE
from knowledge_pipeline.lib.store import count_contents

ASSET_OWNERS = ["team:data-eng"]
ASSET_TAGS = {"domain": "knowledge"}


def _hash_file(path) -> str:
    """SHA-256 hash of a file (first 16 hex chars)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


@dg.asset(
    group_name="shared",
    compute_kind="filesystem",
    owners=ASSET_OWNERS,
    tags=ASSET_TAGS,
    code_version="1",
    description="Copy raw_store.db from static dataset to local data/",
)
def raw_store_copy(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Copy the static dataset using SQLite backup API for a consistent snapshot."""

    source = SOURCE_RAW_STORE  # Define the pinned version of raw_store
    if not source.exists():
        raise FileNotFoundError(f"Source database not found: {source}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    dest = DATA_DIR / "raw_store.db"

    src_conn = sqlite3.connect(source)
    dst_conn = sqlite3.connect(dest)
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    size = dest.stat().st_size
    corpus_hash = _hash_file(dest)
    row_count = count_contents(db_path=dest)

    context.log.info(
        "Copied raw_store.db (%d bytes, %d rows, hash=%s) to %s",
        size,
        row_count,
        corpus_hash,
        dest,
    )
    return dg.MaterializeResult(
        metadata={
            "size_bytes": dg.MetadataValue.int(size),
            "row_count": dg.MetadataValue.int(row_count),
            "corpus_hash": dg.MetadataValue.text(corpus_hash),
            "source": dg.MetadataValue.path(str(source)),
        }
    )
