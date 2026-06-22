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
    entity_id    TEXT NOT NULL PRIMARY KEY REFERENCES entities (entity_id) ON DELETE CASCADE,
    file_path    TEXT NOT NULL UNIQUE,            -- flat: {slug}-{shortid}.md under data/wiki/
    related_ids  TEXT,                            -- JSON array of related entity_ids
    updated_at   TEXT NOT NULL                    -- ISO-8601 UTC
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
