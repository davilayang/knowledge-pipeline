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
-- pages — one rendered page file per entity (1:1, FK to entities).
-- page_type/slug live on `entities` (authoritative); not duplicated here.
-- file_path is UNIQUE so a slug+shortid collision surfaces instead of silently
-- overwriting. related_ids is a JSON array of related entity_ids (advisory; not
-- an FK). num_sources is derived on read from the attributed lane
-- (attributed.count_sources_for_entity), not stored.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pages (
    entity_id       TEXT NOT NULL PRIMARY KEY REFERENCES entities (entity_id) ON DELETE CASCADE,
    file_path       TEXT NOT NULL UNIQUE,         -- flat: {slug}-{shortid}.md under data/wiki/
    related_ids     TEXT,                         -- JSON array of related entity_ids
    updated_at      TEXT NOT NULL                 -- ISO-8601 UTC
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
-- entity_relations — accumulated entity↔entity co-occurrence edges (#54).
-- A pure LEDGER: one row per (directed edge, contributing content item). The
-- link strength `co_count` is DERIVED on read (COUNT(DISTINCT item_id)), so it's
-- retry-safe by construction (idempotent ON CONFLICT DO NOTHING, no counter to
-- double-bump).
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
-- while the regenerable tables around it are recreated. The from-empty rebuild
-- runbook must `.dump rejected_entities` and reseed it (the option-(b) tax).
-- Synthesis reads this directly; Notion is a redundant edit UI synced in.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rejected_entities (
    normalized_name TEXT NOT NULL PRIMARY KEY,    -- match key: lower/trim/collapse-ws
    category        TEXT,
    reason          TEXT,
    rejected_at     TEXT NOT NULL                  -- ISO-8601 UTC
) STRICT;

-- ===========================================================================
-- ATTRIBUTED LANE (claim-centric) — sources / claims / claim_entities.
--
-- The wiki stores claims ATTRIBUTED to their sources ("a Medium piece in
-- Codrift (2026-03) claimed X") and renders an entity page from them, instead
-- of synthesising prose from raw-article spans. The three tables below are the
-- claim-centric core; identity stays in `entities`/`aliases` (above), which
-- they FK into. Counts (num_sources) and page-worthiness are DERIVED on read
-- from these rows, never stored. Helpers live in domains/wiki/attributed.py.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- sources — the source registry + attribution metadata (who said it, where,
-- when). content_key is the normalized dedup key (canonical URL /
-- <source>::<url>), UNIQUE so a re-fetch of the same article UPSERTs onto one
-- row rather than forking it; source_id is the stable surrogate claims FK to.
-- publication/author/published_at are what a rendered page attributes a claim
-- with; content_hash/fetched_at record the fetched body so an article change is
-- detectable across synthesis runs.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sources (
    source_id     TEXT NOT NULL PRIMARY KEY,        -- src_<16hex>
    content_key   TEXT NOT NULL UNIQUE,             -- normalized canonical URL / <source>::<url>
    origin_type   TEXT NOT NULL,                    -- queue / raw_store / session / research
    title         TEXT,
    author        TEXT,
    publication   TEXT,                             -- e.g. the Medium publication (for attribution)
    url           TEXT,
    published_at  TEXT,                             -- publication date (ISO-8601) or NULL
    content_hash  TEXT,                             -- fetched-body hash — detect article change
    fetched_at    TEXT,                             -- ISO-8601 UTC (when the body was fetched)
    added_at      TEXT NOT NULL                     -- ISO-8601 UTC (first sighting)
) STRICT;

-- ---------------------------------------------------------------------------
-- claims — one atomic statement as asserted by ONE source, tagged `reported`
-- (the source presents it as fact) or `opinion` (prediction / opinion /
-- unverified). text_hash is sha256 of the normalized claim text; the
-- UNIQUE(source_id, text_hash) key makes a re-run idempotent — re-extracting a
-- source's claims re-inserts the same rows without duplicating. ON DELETE
-- CASCADE so dropping a source removes its claims.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS claims (
    claim_id    TEXT NOT NULL PRIMARY KEY,          -- clm_<16hex>
    source_id   TEXT NOT NULL REFERENCES sources (source_id) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    text_hash   TEXT NOT NULL,                      -- sha256(normalized text) — idempotency
    claim_kind  TEXT NOT NULL CHECK (claim_kind IN ('reported', 'opinion')),
    created_at  TEXT NOT NULL,                      -- ISO-8601 UTC
    UNIQUE (source_id, text_hash)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_claims_source ON claims (source_id);

-- ---------------------------------------------------------------------------
-- claim_entities — the many-to-many bridge from a claim to the entity(ies) it
-- is ABOUT (its subject(s), 0..N, from subject-attribution). A page for entity
-- E renders the claims joined through here (see attributed_claims_for_entity).
-- Composite PK makes a re-run idempotent; both FKs ON DELETE CASCADE so
-- dropping a claim or an entity prunes its bridge rows.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS claim_entities (
    claim_id   TEXT NOT NULL REFERENCES claims (claim_id) ON DELETE CASCADE,
    entity_id  TEXT NOT NULL REFERENCES entities (entity_id) ON DELETE CASCADE,
    PRIMARY KEY (claim_id, entity_id)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_claim_entities_entity ON claim_entities (entity_id, claim_id);
