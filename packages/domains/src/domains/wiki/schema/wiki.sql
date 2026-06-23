-- Wiki state schema for data/wiki.db (SQLite).
--
-- Identity is an opaque surrogate (entities.entity_id = e_<16hex>), decoupled
-- from page_type/slug so an entity the LLM types inconsistently no longer
-- splits into two pages. `entities` is the identity record; `pages` is the
-- synthesised-artifact record (1:1, FK to entities). All DDL is idempotent and
-- STRICT (column types enforced at write). Rebuild-don't-migrate: drop the file
-- and re-synthesise.

-- ---------------------------------------------------------------------------
-- entities — the identity record (authoritative for canonical_name/slug/page_type)
--
-- entity_id is an opaque surrogate minted ONCE by the system, never recomputed
-- (name-independent → survives renames). normalized_name (lower/trim/collapse-ws
-- of canonical_name) is the deterministic dedup key — exact match is
-- authoritative; fuzzy is advisory only (near-dupes go to the curated merge).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entities (
    entity_id       TEXT NOT NULL PRIMARY KEY,    -- e_<16hex> opaque surrogate
    canonical_name  TEXT NOT NULL,                -- display name (first sighting wins)
    normalized_name TEXT NOT NULL UNIQUE,         -- match key: lower/trim/collapse-ws
    slug            TEXT NOT NULL,                 -- system-generated, minted once
    page_type       TEXT NOT NULL,                 -- open-domain type label (metadata, NOT identity)
    created_at      TEXT NOT NULL                  -- ISO-8601 UTC
) STRICT;

-- ---------------------------------------------------------------------------
-- processed_items — per (item_id, source_type) synthesis-attempt ledger.
-- source_type in the PK so items from different sources never collide.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS processed_items (
    item_id        TEXT NOT NULL,
    source_type    TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('ok', 'error', 'skipped')),
    error          TEXT,                           -- NULL on ok
    processed_at   TEXT NOT NULL,                  -- ISO-8601 UTC
    PRIMARY KEY (item_id, source_type)
) STRICT;

-- ---------------------------------------------------------------------------
-- pages — one synthesised page file per entity (1:1, FK to entities).
-- page_type/slug live on `entities` (authoritative); not duplicated here.
-- file_path is UNIQUE so a slug+shortid collision surfaces instead of silently
-- overwriting. related_ids is a JSON array of related entity_ids (advisory; not
-- an FK). num_sources is derived from page_sources, not stored.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pages (
    entity_id       TEXT NOT NULL PRIMARY KEY REFERENCES entities (entity_id) ON DELETE CASCADE,
    file_path       TEXT NOT NULL UNIQUE,         -- flat: {slug}-{shortid}.md under data/wiki/
    related_ids     TEXT,                         -- JSON array of related entity_ids
    updated_at      TEXT NOT NULL,                -- ISO-8601 UTC
    content_hash    TEXT,                         -- HEAD: semantic hash of the current edition (#47)
    current_version INTEGER                       -- HEAD: max page_versions.version (forward pointer)
) STRICT;

-- ---------------------------------------------------------------------------
-- aliases — alias → entity. `alias` is the display form; `normalized_alias`
-- (lower/trim/collapse-ws) is the globally-unique match key (first-writer-wins
-- under ON CONFLICT). canonical_name lives on `entities`, not here.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aliases (
    alias            TEXT NOT NULL,               -- display form
    normalized_alias TEXT NOT NULL PRIMARY KEY,   -- match key (globally unique)
    entity_id        TEXT NOT NULL REFERENCES entities (entity_id) ON DELETE CASCADE
) STRICT;

CREATE INDEX IF NOT EXISTS idx_aliases_entity_id ON aliases (entity_id);

-- ---------------------------------------------------------------------------
-- page_sources — (entity_id, item_id, source_type) contribution ledger.
-- Ground truth for num_sources = COUNT(DISTINCT item_id) per entity_id.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS page_sources (
    entity_id      TEXT NOT NULL REFERENCES entities (entity_id) ON DELETE CASCADE,
    item_id        TEXT NOT NULL,
    source_type    TEXT NOT NULL,
    added_at       TEXT NOT NULL,                 -- ISO-8601 UTC
    PRIMARY KEY (entity_id, item_id, source_type)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_page_sources_entity_id ON page_sources (entity_id);

-- ---------------------------------------------------------------------------
-- page_versions — immutable edition history per entity (#47).
-- Each row is the full semantic content of a page at one version: the body is
-- stored verbatim (no deltas) so any past edition reconstructs without a
-- rebuild. content_hash is the semantic hash that gated the append (see
-- identity.page_content_hash); source_id/source_type tie the edition to the
-- content item that triggered it ((item_id, source_type) is the repo's stable
-- source identity). version is monotonic per entity; HEAD is the max version.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS page_versions (
    entity_id    TEXT NOT NULL REFERENCES entities (entity_id) ON DELETE CASCADE,
    version      INTEGER NOT NULL,                -- monotonic per entity
    created_at   TEXT NOT NULL,                   -- ISO-8601 UTC
    content_hash TEXT NOT NULL,                   -- semantic hash that gated this append
    summary      TEXT NOT NULL DEFAULT '',
    num_sources  INTEGER NOT NULL CHECK (num_sources >= 0),  -- source count at this edition
    source_id    TEXT NOT NULL,                   -- content item that triggered the change
    source_type  TEXT NOT NULL,                   -- (source_id, source_type) = stable source identity
    content      TEXT NOT NULL,                   -- full markdown body at this version
    PRIMARY KEY (entity_id, version)
) STRICT;

-- ---------------------------------------------------------------------------
-- entity_relations — accumulated entity↔entity co-occurrence edges (#54).
-- A pure LEDGER: one row per (directed edge, contributing content item). The
-- link strength `co_count` is DERIVED on read (COUNT(DISTINCT item_id)), exactly
-- as num_sources is derived over page_sources — so it's retry-safe by
-- construction (idempotent ON CONFLICT DO NOTHING, no counter to double-bump).
-- entity_id/related_entity_id are graph NODES (both FK→entities); item_id/
-- source_type are the PROVENANCE (which article co-mentioned them), the repo's
-- stable (item_id, source_type) content identity. Edges are inserted in BOTH
-- directions, so get_related_for_entity reads one column.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entity_relations (
    entity_id         TEXT NOT NULL REFERENCES entities (entity_id) ON DELETE CASCADE,
    related_entity_id TEXT NOT NULL REFERENCES entities (entity_id) ON DELETE CASCADE,
    item_id           TEXT NOT NULL,
    source_type       TEXT NOT NULL,
    added_at          TEXT NOT NULL,                -- ISO-8601 UTC
    PRIMARY KEY (entity_id, related_entity_id, item_id, source_type)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_entity_relations_entity_id ON entity_relations (entity_id);

-- ---------------------------------------------------------------------------
-- rejected_entities — the curator denylist (#15). AUTHORED, not regenerable:
-- a human's decision that a name should never become a page. Name-keyed
-- (surrogate ids are random per mint and change on a from-empty rebuild;
-- normalized names are stable), so this table survives "rebuild-don't-migrate"
-- while the 7 regenerable tables above are recreated. The from-empty rebuild
-- runbook must `.dump rejected_entities` and reseed it (the option-(b) tax).
-- Synthesis reads this directly; Notion is a redundant edit UI synced in.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rejected_entities (
    normalized_name TEXT NOT NULL PRIMARY KEY,    -- match key: lower/trim/collapse-ws
    category        TEXT,
    reason          TEXT,
    rejected_at     TEXT NOT NULL                  -- ISO-8601 UTC
) STRICT;
