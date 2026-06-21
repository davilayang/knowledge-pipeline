-- Wiki schema for the knowledge_pipeline database.
--
-- Three tables track synthesis state, page metadata, and alias resolution.
-- All DDL is idempotent (IF NOT EXISTS) — safe to re-run without a migration
-- framework. Drop and re-run to rebuild from scratch (see plan §State management).
--
-- Apply against the knowledge_pipeline database:
--   psql -d knowledge_pipeline -f wiki.sql

CREATE SCHEMA IF NOT EXISTS wiki;

-- ---------------------------------------------------------------------------
-- wiki.processed
--
-- One row per (item_id, source_type) pair that has been attempted.
-- source_type is part of the PK so rows from different sources never collide
-- (e.g. newsletter vs. reforge vs. journal).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wiki.processed (
    item_id        text        NOT NULL,
    source_type    text        NOT NULL,
    status         text        NOT NULL CHECK (status IN ('ok', 'error', 'skipped')),
    error          text,                   -- NULL on ok
    processed_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (item_id, source_type)
);

-- ---------------------------------------------------------------------------
-- wiki.pages
--
-- One row per synthesised entity page.
-- related / sources / source_types are jsonb arrays (not json) so Postgres
-- can index and query them efficiently if needed later.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wiki.pages (
    entity_id      text        NOT NULL PRIMARY KEY,
    page_type      text        NOT NULL,   -- 'concept' | 'tool' | 'trend'
    file_path      text        NOT NULL,   -- relative path under data/wiki/
    related        jsonb,                  -- list of related entity_ids
    sources        jsonb,                  -- list of source item_ids
    source_types   jsonb,                  -- list of source_type strings
    updated_at     timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- wiki.aliases
--
-- One row per (entity_id, alias) pair. alias is globally unique — each alias
-- maps to exactly one canonical entity (first-writer-wins under ON CONFLICT).
-- canonical_name is denormalised onto every row for read-side convenience;
-- in the rare case where two writers disagree on canonical_name for the same
-- entity_id, snapshot_aliases() picks the lexicographically-first row's
-- canonical (deterministic). Promote canonical to its own entities table if
-- divergence becomes load-bearing.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wiki.aliases (
    entity_id      text NOT NULL,
    canonical_name text NOT NULL,
    alias          text NOT NULL,
    UNIQUE (alias)
);

-- Index for lookups by entity_id (e.g. "give me all aliases for X").
CREATE INDEX IF NOT EXISTS wiki_aliases_entity_id_idx
    ON wiki.aliases (entity_id);

-- ---------------------------------------------------------------------------
-- wiki.page_sources
--
-- One row per (entity_id, item_id, source_type) contribution — the
-- deterministic record of which content item surfaced which entity. Written
-- by the commit node in the same all-or-nothing transaction as wiki.pages /
-- wiki.processed, ON CONFLICT DO NOTHING (idempotent under retries).
--
-- Ground truth for num_sources: COUNT(DISTINCT item_id) per entity_id. This
-- replaces the LLM-authored wiki.pages.sources jsonb array as the count source
-- (that list stays for display only). source_type is part of the key because
-- item_id is only unique within a source (mirrors wiki.processed's PK).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wiki.page_sources (
    entity_id      text        NOT NULL,
    item_id        text        NOT NULL,
    source_type    text        NOT NULL,
    added_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (entity_id, item_id, source_type)
);

-- Index for the count/lookup by entity ("num_sources for X", "which items built X").
CREATE INDEX IF NOT EXISTS wiki_page_sources_entity_id_idx
    ON wiki.page_sources (entity_id);
