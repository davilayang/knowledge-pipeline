# Source asset specs for upstream stores owned by newsletter-assistant.
#
# These are declarative-only AssetSpecs — they don't materialize. Their
# purpose is to anchor lineage: pipelines that read from raw_store /
# sessions / notes declare deps on these specs, and the Dagster UI
# renders shared upstream nodes connecting backup_readings and
# synthesize_wiki (and future per-source discoverers).
#
# Owned by newsletter-assistant; we consume read-only.

import dagster as dg

UPSTREAM_GROUP = "upstream"

raw_store_source = dg.AssetSpec(
    key=["raw_store"],
    group_name=UPSTREAM_GROUP,
    description=(
        "newsletter-assistant SQLite — `contents` table holds newsletter "
        "scrapes (article markdown + metadata). Consumed by "
        "snapshot_raw_store (backup_readings) and discover_pending_contents "
        "(synthesize_wiki)."
    ),
    metadata={
        "owner": dg.MetadataValue.text("newsletter-assistant"),
        "path": dg.MetadataValue.path("data/raw_store.db"),
    },
)

sessions_source = dg.AssetSpec(
    key=["sessions"],
    group_name=UPSTREAM_GROUP,
    description=(
        "newsletter-assistant SQLite — session turns from user/LLM chats. "
        "Consumed by snapshot_sessions (backup_readings); future consumer: "
        "discover_pending_sessions (synthesize_wiki)."
    ),
    metadata={
        "owner": dg.MetadataValue.text("newsletter-assistant"),
        "path": dg.MetadataValue.path("data/sessions.db"),
    },
)

notes_source = dg.AssetSpec(
    key=["notes"],
    group_name=UPSTREAM_GROUP,
    description=(
        "newsletter-assistant user notes (markdown files). Future consumer: "
        "discover_pending_notes (synthesize_wiki). No current consumer."
    ),
    metadata={
        "owner": dg.MetadataValue.text("newsletter-assistant"),
        "path": dg.MetadataValue.path("data/notes/"),
    },
)


all_sources = [raw_store_source, sessions_source, notes_source]

defs = dg.Definitions(assets=all_sources)
