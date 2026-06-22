-- Wiki state schema for data/wiki.db (SQLite).
--
-- Four tables track synthesis state, page metadata, alias resolution, and the
-- source ledger. All DDL is idempotent (IF NOT EXISTS) — safe to re-run without
-- a migration framework; applied via `create_schema()` (executescript) the same
-- way the sibling SQLite stores (raw_store.db / queue.db) bring themselves up.
-- Drop the file and re-run to rebuild from scratch (rebuild-don't-migrate).

-- ---------------------------------------------------------------------------
-- processed
--
-- One row per (item_id, source_type) pair that has been attempted.
-- source_type is part of the PK so rows from different sources never collide.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS processed (
    item_id        TEXT NOT NULL,
    source_type    TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('ok', 'error', 'skipped')),
    error          TEXT,                                   -- NULL on ok
    processed_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (item_id, source_type)
);

-- ---------------------------------------------------------------------------
-- pages
--
-- One row per synthesised entity page. related / sources / source_types are
-- JSON arrays stored as TEXT (SQLite has no native array/jsonb type); callers
-- json.loads them on read.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pages (
    entity_id      TEXT NOT NULL PRIMARY KEY,
    page_type      TEXT NOT NULL,                          -- 'concept' | 'tool' | 'trend'
    file_path      TEXT NOT NULL,                          -- relative path under data/wiki/
    related        TEXT,                                   -- JSON array of related entity_ids
    sources        TEXT,                                   -- JSON array of source item_ids
    source_types   TEXT,                                   -- JSON array of source_type strings
    updated_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- aliases
--
-- One row per (entity_id, alias) pair. alias is globally unique — each alias
-- maps to exactly one canonical entity (first-writer-wins under ON CONFLICT).
-- canonical_name is denormalised onto every row for read-side convenience.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aliases (
    entity_id      TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    alias          TEXT NOT NULL,
    UNIQUE (alias)
);

CREATE INDEX IF NOT EXISTS aliases_entity_id_idx ON aliases (entity_id);

-- ---------------------------------------------------------------------------
-- page_sources
--
-- One row per (entity_id, item_id, source_type) contribution — the
-- deterministic record of which content item surfaced which entity. Written
-- in the same all-or-nothing transaction as pages / processed, ON CONFLICT DO
-- NOTHING (idempotent under retries). Ground truth for num_sources:
-- COUNT(DISTINCT item_id) per entity_id.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS page_sources (
    entity_id      TEXT NOT NULL,
    item_id        TEXT NOT NULL,
    source_type    TEXT NOT NULL,
    added_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entity_id, item_id, source_type)
);

CREATE INDEX IF NOT EXISTS page_sources_entity_id_idx ON page_sources (entity_id);
