# Asset: copy raw_store.db from newsletter-assistant to local data/.

import sqlite3

import dagster as dg
from dagster import AssetExecutionContext

from knowledge_pipeline.config import DATA_DIR, SOURCE_RAW_STORE

ASSET_OWNERS = ["team:data-eng"]
ASSET_TAGS = {"domain": "knowledge"}


# TODO: Replace with live access with tunnel using datasette? or another way?
@dg.asset(
    group_name="rag_0_baseline",
    compute_kind="filesystem",
    owners=ASSET_OWNERS,
    tags=ASSET_TAGS,
    code_version="1",
    description="Copy raw_store.db from newsletter-assistant to local data/",
)
def raw_store_copy(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Copy the source database using SQLite backup API for a consistent snapshot."""
    source = SOURCE_RAW_STORE
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
    context.log.info("Copied raw_store.db (%d bytes) to %s", size, dest)
    return dg.MaterializeResult(
        metadata={
            "size_bytes": dg.MetadataValue.int(size),
            "source": dg.MetadataValue.path(str(source)),
        }
    )
