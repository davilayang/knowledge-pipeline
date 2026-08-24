"""Regression guard for the hand-run claim_kind -> provenance + stance migration.

This migration is executed once, by hand, over SSH, against a production wiki.db
holding thousands of claims. It is the highest-risk artefact in the repo and it
already shipped one total-loss defect that no test would have caught: without
`.bail on`, a second run failed at the INSERT, continued past the error, dropped
the real `claims` table and renamed an empty one over it.

So the guards are tested here rather than trusted. Each test corresponds to a
failure mode that was reproduced, not imagined.
"""

import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "migrations"
    / "2026-08-24_claims_provenance_stance.sql"
)

pytestmark = pytest.mark.skipif(
    shutil.which("sqlite3") is None, reason="needs the sqlite3 CLI (the migration is hand-run)"
)

# The pre-migration shape, reproduced verbatim: single claim_kind column, STRICT,
# with claim_entities cascading on claims — the cascade is what makes the
# foreign_keys pragma load-bearing.
_OLD_SCHEMA = """
CREATE TABLE sources (
    source_id TEXT NOT NULL PRIMARY KEY,
    content_key TEXT NOT NULL UNIQUE,
    origin_type TEXT NOT NULL,
    added_at TEXT NOT NULL
) STRICT;
CREATE TABLE entities (entity_id TEXT NOT NULL PRIMARY KEY) STRICT;
CREATE TABLE claims (
    claim_id TEXT NOT NULL PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources (source_id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    claim_kind TEXT NOT NULL CHECK (claim_kind IN ('reported', 'opinion', 'derived')),
    created_at TEXT NOT NULL,
    UNIQUE (source_id, text_hash)
) STRICT;
CREATE INDEX idx_claims_source ON claims (source_id);
CREATE TABLE claim_entities (
    claim_id TEXT NOT NULL REFERENCES claims (claim_id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL REFERENCES entities (entity_id) ON DELETE CASCADE,
    PRIMARY KEY (claim_id, entity_id)
) STRICT;
INSERT INTO sources VALUES
    ('s_q', 'medium::x', 'queue', '2026-07-01'),
    ('s_n', 'local:note.md', 'note', '2026-08-23');
INSERT INTO entities VALUES ('e1');
INSERT INTO claims VALUES
    ('c1', 's_q', 'a', 'h1', 'reported', '2026-07-01'),
    ('c2', 's_q', 'b', 'h2', 'opinion',  '2026-07-01'),
    ('c3', 's_n', 'my note', 'h3', 'derived', '2026-08-23');
INSERT INTO claim_entities VALUES ('c1', 'e1'), ('c2', 'e1'), ('c3', 'e1');
"""


def _old_db(tmp_path: Path) -> Path:
    db = tmp_path / "wiki.db"
    con = sqlite3.connect(db)
    con.executescript(_OLD_SCHEMA)
    con.commit()
    con.close()
    return db


def _run_migration(db: Path, *, cli_bail: bool = True) -> subprocess.CompletedProcess:
    """Run the migration the way an operator does.

    `cli_bail=False` drops the `-bail` FLAG so the script's own `.bail on` is
    what is under test. Without that distinction a test passing `-bail` proves
    nothing about the file: the flag alone stops the run, so deleting `.bail on`
    from the script would leave every test green while restoring a
    total-data-loss path for any operator who forgets the flag.
    """
    argv = ["sqlite3"] + (["-bail"] if cli_bail else []) + [str(db)]
    return subprocess.run(argv, stdin=MIGRATION.open(), capture_output=True, text=True)


def _rows(db: Path, sql: str) -> list[tuple]:
    con = sqlite3.connect(db)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_maps_every_row_onto_the_two_axes(tmp_path):
    db = _old_db(tmp_path)
    assert _run_migration(db).returncode == 0
    assert _rows(db, "SELECT claim_id, provenance, stance FROM claims ORDER BY claim_id") == [
        ("c1", "source", "reported"),
        ("c2", "source", "opinion"),
        # the promoted note becomes the user's, not the pipeline's — the whole
        # reason the column was split
        ("c3", "user", None),
    ]


def test_claim_entity_links_survive_the_table_swap(tmp_path):
    # claim_entities cascades on claims, so DROP TABLE claims with foreign_keys
    # enforced deletes every link. Reproduced before the pragma was added: 3 of 3.
    db = _old_db(tmp_path)
    assert _run_migration(db).returncode == 0
    assert _rows(db, "SELECT COUNT(*) FROM claim_entities")[0][0] == 3
    assert _rows(db, "PRAGMA foreign_key_check") == []


def test_index_and_unique_constraint_survive(tmp_path):
    # A rebuild only keeps what it re-declares.
    db = _old_db(tmp_path)
    assert _run_migration(db).returncode == 0
    assert _rows(db, "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_claims_source'")
    con = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO claims VALUES "
                "('cX','s_q','dupe','h1','source','reported','2026-08-24')"
            )
    finally:
        con.close()


def test_a_second_run_leaves_the_data_intact(tmp_path):
    # THE defect this file exists for. Without `.bail on` the sqlite3 CLI runs
    # past the failed INSERT, drops the real table and renames an empty one over
    # it — a fixture went from 2 claims to 0 before the guard was added.
    db = _old_db(tmp_path)
    assert _run_migration(db).returncode == 0
    before = _rows(db, "SELECT claim_id, provenance, stance FROM claims ORDER BY claim_id")

    # No -bail flag: the script's OWN `.bail on` must stop this, because that is
    # the guard that survives an operator who mistypes the invocation.
    second = _run_migration(db, cli_bail=False)
    assert second.returncode != 0  # must abort, not proceed
    assert _rows(db, "SELECT claim_id, provenance, stance FROM claims ORDER BY claim_id") == before


def test_refuses_to_run_when_a_claim_has_no_source(tmp_path):
    # An orphan survives the LEFT JOIN copy but then violates the claims->sources
    # FK once enforcement returns, leaving an inconsistency nothing reports. The
    # script must refuse before anything is dropped.
    db = _old_db(tmp_path)
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=OFF")
    con.execute("INSERT INTO claims VALUES ('c9','s_GONE','orphan','h9','derived','2026-08-23')")
    con.commit()
    con.close()

    assert _run_migration(db).returncode != 0
    # untouched: still the old shape
    cols = {r[1] for r in _rows(db, "PRAGMA table_info(claims)")}
    assert "claim_kind" in cols and "provenance" not in cols


def test_cross_axis_invariant_is_enforced_after_migrating(tmp_path):
    # Only a source has a stance. The per-column CHECKs each admit
    # ('user','reported') and ('source', NULL); this pair must not be storable,
    # or one claim renders twice and the page summary comes out empty.
    db = _old_db(tmp_path)
    assert _run_migration(db).returncode == 0
    con = sqlite3.connect(db)
    try:
        for provenance, stance in (("user", "reported"), ("source", None)):
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO claims VALUES ('cX','s_q','t','hX',?,?,'2026-08-24')",
                    (provenance, stance),
                )
    finally:
        con.close()
