-- Split claims.claim_kind into two orthogonal columns: provenance + stance.
--
-- WHY: claim_kind mixed two independent axes in one enum — how a SOURCE
-- presented a statement ('reported' vs 'opinion') and WHO authored it
-- ('derived'). Because there was no value meaning "the user wrote this",
-- promote_notes stored user-authored notes as claim_kind='derived', i.e. under
-- the value meaning "produced by the pipeline". This migration gives user
-- material its own provenance and frees 'derived' for genuine pipeline output.
--
-- Run once on each deployed wiki.db BEFORE deploying the split.
-- Fresh installs get the new shape from CREATE TABLE in domains/wiki/schema/wiki.sql
-- (which is IF NOT EXISTS, so it does NOT alter an existing db — hence this file).
--
-- Usage:
--   ssh hcloud
--   cp /path/to/wiki.db /path/to/wiki.db.bak-2026-08-24     # take the backup first
--   sqlite3 -bail /path/to/wiki.db < 2026-08-24_claims_provenance_stance.sql
--
-- `.bail on` is set below so the -bail flag is belt-and-braces rather than the
-- only guard. DO NOT REMOVE IT. The sqlite3 CLI continues past errors by
-- default, and this script's INSERT is followed by a DROP: without bail, a
-- failed INSERT still proceeds to drop the real table and rename an EMPTY one
-- over it. Reproduced 2026-08-24 on a fixture — a second run took the table
-- from 2 claims to 0. An earlier revision of this header wrongly described a
-- rerun as a safe failure mode; it is a total loss of the claims table.
--
-- The table is STRICT with a CHECK constraint, so SQLite cannot ALTER it in
-- place; this is the full rebuild. foreign_keys MUST be OFF for the swap:
-- claim_entities holds REFERENCES claims(claim_id) ON DELETE CASCADE, so
-- DROP TABLE claims with enforcement ON would cascade every link row away
-- (reproduced: 4 of 4 links destroyed).

.bail on

PRAGMA foreign_keys = OFF;

BEGIN;

-- Guard 1 — refuse to run if any claim has no source row.
-- Such a row would survive the copy (the INSERT below LEFT JOINs) but then
-- violate the claims->sources FK the moment enforcement is switched back on,
-- leaving the db inconsistent in a way nothing here would report. Prod had 0
-- orphans on 2026-08-23; if that changes, fix the data first, deliberately.
-- The CHECK is the abort mechanism: a non-zero count raises, and `.bail on`
-- stops the script before anything is dropped.
CREATE TEMP TABLE _guard_orphans (n INTEGER CHECK (n = 0));
INSERT INTO _guard_orphans
SELECT COUNT(*) FROM claims c
LEFT JOIN sources s ON s.source_id = c.source_id
WHERE s.source_id IS NULL;

CREATE TABLE claims_new (
    claim_id    TEXT NOT NULL PRIMARY KEY,          -- clm_<16hex>
    source_id   TEXT NOT NULL REFERENCES sources (source_id) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    text_hash   TEXT NOT NULL,                      -- sha256(normalized text) — idempotency
    provenance  TEXT NOT NULL CHECK (provenance IN ('source', 'user', 'derived')),
    stance      TEXT          CHECK (stance     IN ('reported', 'opinion')),
    created_at  TEXT NOT NULL,                      -- ISO-8601 UTC
    -- Cross-axis invariant: only a source has a stance. The per-column CHECKs
    -- above would each pass for ('user','reported') or ('source', NULL); this
    -- one rejects both, so the two axes cannot drift into meaningless pairs.
    CHECK ((provenance = 'source') = (stance IS NOT NULL)),
    UNIQUE (source_id, text_hash)
) STRICT;

-- reported/opinion were always source-authored; the old value becomes the stance.
-- 'derived' rows are split by their source's origin_type: a note is the user's
-- own writing (-> 'user'); anything else stays 'derived'. As of 2026-08-24 the
-- second branch matches zero rows — every derived claim in prod came from a note.
INSERT INTO claims_new (claim_id, source_id, text, text_hash, provenance, stance, created_at)
SELECT
    c.claim_id,
    c.source_id,
    c.text,
    c.text_hash,
    CASE
        WHEN c.claim_kind IN ('reported', 'opinion') THEN 'source'
        WHEN c.claim_kind = 'derived' AND s.origin_type = 'note' THEN 'user'
        ELSE 'derived'
    END,
    CASE WHEN c.claim_kind IN ('reported', 'opinion') THEN c.claim_kind ELSE NULL END,
    c.created_at
FROM claims c
-- LEFT, not INNER: an INNER JOIN would SILENTLY DROP any claim whose source row
-- is missing. Guard 1 already refuses to run in that case, so this is the second
-- layer — a dropped claim leaves no trace, and belt-and-braces is cheap here.
LEFT JOIN sources s ON s.source_id = c.source_id;

-- Guard 2 — refuse to swap unless every row copied.
CREATE TEMP TABLE _guard_rowcount (ok INTEGER CHECK (ok = 1));
INSERT INTO _guard_rowcount
SELECT (SELECT COUNT(*) FROM claims_new) = (SELECT COUNT(*) FROM claims);

DROP TABLE claims;
ALTER TABLE claims_new RENAME TO claims;

CREATE INDEX IF NOT EXISTS idx_claims_source ON claims (source_id);

DROP TABLE _guard_orphans;
DROP TABLE _guard_rowcount;

COMMIT;

PRAGMA foreign_keys = ON;

-- Guard 3 — post-swap referential integrity. foreign_key_check RETURNS
-- violation rows rather than failing, so it cannot abort anything by itself;
-- funnel it through a CHECK so a violation is loud instead of advisory.
CREATE TEMP TABLE _guard_fk (n INTEGER CHECK (n = 0));
INSERT INTO _guard_fk SELECT COUNT(*) FROM pragma_foreign_key_check;
DROP TABLE _guard_fk;

-- Expected on prod as measured 2026-08-23:
--   source/reported 3901 | source/opinion 3594 | user/(null) 3 | derived/(null) 0
SELECT provenance, COALESCE(stance, '(null)') AS stance, COUNT(*) AS n
FROM claims GROUP BY 1, 2 ORDER BY 3 DESC;
