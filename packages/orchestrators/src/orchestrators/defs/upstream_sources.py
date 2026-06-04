# Declarative-only AssetSpecs that anchor lineage for the underlying
# SQLite / filesystem stores consumed by this repo's pipelines. They
# don't materialize — they exist so downstream assets (backup_readings,
# synthesize_wiki, future per-source discoverers) can declare deps
# against a stable upstream node and the graph renders connected rather
# than orphaned. `owner` metadata names the writer.

import dagster as dg

UPSTREAM_GROUP = "upstream"

raw_store_source = dg.AssetSpec(
    key=["raw_store"],
    group_name=UPSTREAM_GROUP,
    description=(
        "newsletter-assistant SQLite — `contents` table holds newsletter "
        "scrapes (article markdown + metadata). Consumed by "
        "snapshot_raw_store (backup_readings) and wiki/synthesized "
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
        "synthesize_wiki sessions discovery."
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
        "synthesize_wiki notes discovery. No current consumer."
    ),
    metadata={
        "owner": dg.MetadataValue.text("newsletter-assistant"),
        "path": dg.MetadataValue.path("data/notes/"),
    },
)


research_source = dg.AssetSpec(
    key=["research"],
    group_name=UPSTREAM_GROUP,
    description=(
        "newsletter-assistant SQLite — research-panel output (Claude/Gemini "
        "CLI sessions against `data/codebases/`). Consumed by "
        "snapshot_research (backup_readings)."
    ),
    metadata={
        "owner": dg.MetadataValue.text("newsletter-assistant"),
        "path": dg.MetadataValue.path("data/research.db"),
    },
)

queue_store_source = dg.AssetSpec(
    key=["queue_store"],
    group_name=UPSTREAM_GROUP,
    description=(
        "knowledge-pipeline SQLite — `queue_items` + `extraction_calls` "
        "tables. Written by triage_queued_items and extract_complex_contents "
        "assets in this repo. Consumed by snapshot_queue (backup_readings)."
    ),
    metadata={
        "owner": dg.MetadataValue.text("knowledge-pipeline"),
        "path": dg.MetadataValue.path("data/queue.db"),
    },
)

notion_queue_source = dg.AssetSpec(
    key=["notion_queue"],
    group_name=UPSTREAM_GROUP,
    description=(
        "Notion 'Knowledge OS Queue' database — user-facing capture surface. "
        "Consumed by triage_queued_items/triaged and extract_complex_contents "
        "(sensor + published writeback)."
    ),
    metadata={
        "owner": dg.MetadataValue.text("user"),
        "path": dg.MetadataValue.text("Notion: NOTION_QUEUE_DB_ID"),
    },
)


all_sources = [
    raw_store_source,
    sessions_source,
    notes_source,
    research_source,
    queue_store_source,
    notion_queue_source,
]

defs = dg.Definitions(assets=all_sources)
