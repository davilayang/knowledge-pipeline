"""Claim-centric wiki.db helpers — the attributed lane's storage layer.

The wiki stores claims ATTRIBUTED to their sources (sources / claims /
claim_entities) and renders an entity page from them, rather than synthesising
prose from raw-article spans. This module is the write + read layer for those
three tables; identity (entities / aliases) stays in `domains.wiki.state`, which
these helpers FK into. Counts and page-worthiness are DERIVED on read from these
rows, never stored.

Pure functions over a sqlite3 connection (the caller owns the transaction),
matching `domains.wiki.state`. Open connections with `state.connect` /
`state.connection` (both set `PRAGMA foreign_keys=ON`).
"""

import hashlib
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

import yaml

from domains.wiki.identity import EntityRecord

_SOURCE_COLS = (
    "source_id, content_key, origin_type, title, author, publication, "
    "url, published_at, content_hash, fetched_at, added_at"
)

_CLAIM_COLS = "claim_id, source_id, text, text_hash, claim_kind, created_at"


def mint_source_id(content_key: str) -> str:
    """Deterministic `src_<16hex>` surrogate for a source, keyed on its
    normalized content_key. Stable across runs so a re-synthesis of the same
    article computes the same id — the anchor claims FK to."""
    digest = hashlib.sha256(content_key.encode("utf-8")).hexdigest()[:16]
    return f"src_{digest}"


def mint_claim_id(source_id: str, text_hash: str) -> str:
    """Deterministic `clm_<16hex>` surrogate for a claim, keyed on
    (source_id, text_hash) — the same grain as the claims idempotency key.

    Load-bearing that this is deterministic, not random: a re-run's
    `insert_claim` is `ON CONFLICT DO NOTHING`, so the row keeps its ORIGINAL
    id; the following `insert_claim_entity` must reference that same id or it
    FK-fails. Scoping to source_id (not text_hash alone) keeps the id a global PK
    even when two sources make the identical claim."""
    digest = hashlib.sha256(f"{source_id}\x00{text_hash}".encode()).hexdigest()[:16]
    return f"clm_{digest}"


def claim_text_hash(text: str) -> str:
    """sha256 of the normalized claim text — the idempotency key for a claim
    within its source. Normalization (collapse whitespace, casefold) makes
    cosmetic re-extraction differences (extra spaces, a capitalized first word)
    hash to the same row so a re-run doesn't duplicate the claim."""
    normalized = " ".join(text.split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SourceRecord:
    """One source in the registry plus its attribution metadata.

    `content_key` is the normalized dedup key (canonical URL / <source>::<url>),
    UNIQUE — a re-fetch of the same article UPSERTs onto one row. `source_id` is
    the stable surrogate claims FK to. publication/author/published_at are what a
    rendered page attributes a claim with; content_hash/fetched_at track whether
    the fetched body has changed across synthesis runs.
    """

    source_id: str
    content_key: str
    origin_type: str
    title: str | None
    author: str | None
    publication: str | None
    url: str | None
    published_at: str | None
    content_hash: str | None
    fetched_at: str | None
    added_at: str


def upsert_source(
    conn: sqlite3.Connection, source: SourceRecord, *, synthesized_at: str | None = None
) -> str:
    """Insert or update one source, keyed on `content_key`. Caller manages the
    transaction.

    ON CONFLICT(content_key) DO UPDATE refreshes the mutable attribution/fetch
    metadata so a re-fetched article propagates on the next synthesis run, but
    leaves `source_id`/`content_key`/`added_at` (first-sighting identity)
    untouched — claims already FK'd to the original surrogate aren't orphaned.
    Nullable attribution columns use `COALESCE(excluded, existing)` so a degraded
    re-fetch (a field now NULL) keeps last-known-good rather than clobbering it.
    Returns the SURVIVING source_id (the original one on conflict), so the caller
    FKs claims to the row that actually exists, not the freshly-minted id it may
    have passed in.

    `synthesized_at` is the incremental-sweep watermark — the max(extracted_at)
    the sweep consumed for this source. COALESCE'd like the attribution columns so
    a re-upsert that omits it (the one-off backfill) keeps a watermark a prior
    sweep set rather than wiping it to NULL.
    """
    conn.execute(
        f"INSERT INTO sources ({_SOURCE_COLS}, synthesized_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (content_key) DO UPDATE SET "
        "origin_type = excluded.origin_type, "
        "title = COALESCE(excluded.title, sources.title), "
        "author = COALESCE(excluded.author, sources.author), "
        "publication = COALESCE(excluded.publication, sources.publication), "
        "url = COALESCE(excluded.url, sources.url), "
        "published_at = COALESCE(excluded.published_at, sources.published_at), "
        "content_hash = COALESCE(excluded.content_hash, sources.content_hash), "
        "fetched_at = COALESCE(excluded.fetched_at, sources.fetched_at), "
        "synthesized_at = COALESCE(excluded.synthesized_at, sources.synthesized_at)",
        (
            source.source_id,
            source.content_key,
            source.origin_type,
            source.title,
            source.author,
            source.publication,
            source.url,
            source.published_at,
            source.content_hash,
            source.fetched_at,
            source.added_at,
            synthesized_at,
        ),
    )
    row = conn.execute(
        "SELECT source_id FROM sources WHERE content_key = ?",
        (source.content_key,),
    ).fetchone()
    return row[0]


def get_synthesized_watermarks(conn: sqlite3.Connection) -> dict[str, str]:
    """`{content_key: synthesized_at}` for every source that carries a watermark.

    The incremental sweep reads this once and skips a source iff its content_key
    is present here AND its extraction docs haven't advanced past the recorded
    value. Sources with a NULL watermark (never synthesized) are excluded, so the
    sweep treats them as work to do."""
    rows = conn.execute(
        "SELECT content_key, synthesized_at FROM sources WHERE synthesized_at IS NOT NULL"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def delete_claims_for_source(conn: sqlite3.Connection, source_id: str) -> None:
    """Delete every claim for a source (ON DELETE CASCADE prunes claim_entities).

    Claims are append-only (`UNIQUE(source_id, text_hash)`), so re-processing a
    re-extracted source would ADD its new claims while KEEPING stale ones. The
    incremental sweep calls this before re-inserting a changed source's claims so
    the page reflects only the current extraction (replace, not merge)."""
    conn.execute("DELETE FROM claims WHERE source_id = ?", (source_id,))


def get_source(conn: sqlite3.Connection, source_id: str) -> SourceRecord | None:
    """Return one source row by source_id, or None if absent."""
    row = conn.execute(
        f"SELECT {_SOURCE_COLS} FROM sources WHERE source_id = ?",
        (source_id,),
    ).fetchone()
    return SourceRecord(*row) if row else None


def get_source_keys_by_origin(conn: sqlite3.Connection, origin_type: str) -> list[str]:
    """Return the content_keys of every source with the given origin_type.

    The note→wiki reconcile diffs the stored note-origin keys against the live
    `promote: true` note ids to discover which promoted notes were unpromoted or
    deleted (their derived claims + source rows must go)."""
    rows = conn.execute(
        "SELECT content_key FROM sources WHERE origin_type = ? ORDER BY content_key",
        (origin_type,),
    ).fetchall()
    return [r[0] for r in rows]


def delete_source(conn: sqlite3.Connection, source_id: str) -> None:
    """Delete a source row (ON DELETE CASCADE prunes its claims + claim_entities)."""
    conn.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))


@dataclass(frozen=True)
class ClaimRecord:
    """One atomic claim as asserted by ONE source. `text_hash` is
    `claim_text_hash(text)` — the per-source idempotency key. `claim_kind` is
    'reported' (the source presents it as fact), 'opinion' (prediction /
    opinion / unverified) — both carrying the extractor's `[reported]`/`[opinion]`
    tag — or 'derived' (the user's own synthesis, e.g. a promoted note).
    """

    claim_id: str
    source_id: str
    text: str
    text_hash: str
    claim_kind: str
    created_at: str


def insert_claim(conn: sqlite3.Connection, claim: ClaimRecord) -> str:
    """Insert one claim. Caller manages the transaction and has already upserted
    the parent source (claims.source_id FKs to sources).

    ON CONFLICT(source_id, text_hash) DO NOTHING makes a re-run idempotent — a
    re-extraction re-inserts the same claim text without duplicating the row.
    Returns the SURVIVING claim_id (the existing row's, on conflict), so the
    caller links claim_entities to the row that actually exists rather than the
    id it passed in — a pre-existing row for this (source_id, text_hash) can't
    then leave the link pointing at a non-existent id (FK break)."""
    conn.execute(
        f"INSERT INTO claims ({_CLAIM_COLS}) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (source_id, text_hash) DO NOTHING",
        (
            claim.claim_id,
            claim.source_id,
            claim.text,
            claim.text_hash,
            claim.claim_kind,
            claim.created_at,
        ),
    )
    row = conn.execute(
        "SELECT claim_id FROM claims WHERE source_id = ? AND text_hash = ?",
        (claim.source_id, claim.text_hash),
    ).fetchone()
    return row[0]


def get_claims_for_source(conn: sqlite3.Connection, source_id: str) -> list[ClaimRecord]:
    """Return every claim for a source, ordered by (created_at, claim_id) for
    a deterministic read."""
    rows = conn.execute(
        f"SELECT {_CLAIM_COLS} FROM claims WHERE source_id = ? ORDER BY created_at, claim_id",
        (source_id,),
    ).fetchall()
    return [ClaimRecord(*r) for r in rows]


def get_entities_for_claim(conn: sqlite3.Connection, claim_id: str) -> set[str]:
    """Return the set of entity_ids a claim is linked to (its claim_entities rows)."""
    rows = conn.execute(
        "SELECT entity_id FROM claim_entities WHERE claim_id = ?", (claim_id,)
    ).fetchall()
    return {r[0] for r in rows}


def insert_claim_entity(conn: sqlite3.Connection, *, claim_id: str, entity_id: str) -> None:
    """Record that `claim_id` is ABOUT `entity_id` (one subject of the claim).
    Caller manages the transaction and has already inserted the claim and the
    entity (both FKs). Idempotent (ON CONFLICT DO NOTHING on the composite PK)."""
    conn.execute(
        "INSERT INTO claim_entities (claim_id, entity_id) VALUES (?, ?) "
        "ON CONFLICT (claim_id, entity_id) DO NOTHING",
        (claim_id, entity_id),
    )


@dataclass(frozen=True)
class AttributedClaim:
    """One claim as it renders on an entity's page: the claim text + kind, plus
    the source attribution (author / publication / published_at / url) that makes
    it an ATTRIBUTED statement ("Jane Doe on medium.com (2026-03) claimed …")
    rather than an unsourced assertion. `author` is the primary attributor for
    this corpus (queue.db carries no publication); `publication` is nullable and
    reserved for origins that supply it."""

    text: str
    claim_kind: str
    author: str | None
    publication: str | None
    published_at: str | None
    url: str | None
    title: str | None = None  # source/note title — the attributor for a derived (note) claim
    fetched_at: str | None = (
        None  # when the source was fetched — a distinct recency signal from published_at
    )


def attributed_claims_for_entity(conn: sqlite3.Connection, entity_id: str) -> list[AttributedClaim]:
    """Every claim attributed to `entity_id`, each carrying its source's
    attribution — the row set a page renders from.

    Ordered by `published_at` ascending with undated sources LAST (`NULLS LAST`,
    else undated rows float to the top), then by claim_id for a stable read."""
    rows = conn.execute(
        """
        SELECT c.text, c.claim_kind, s.author, s.publication, s.published_at,
               s.url, s.title, s.fetched_at
        FROM claim_entities ce
        JOIN claims c ON c.claim_id = ce.claim_id
        JOIN sources s ON s.source_id = c.source_id
        WHERE ce.entity_id = ?
        ORDER BY s.published_at IS NULL, s.published_at, c.claim_id
        """,
        (entity_id,),
    ).fetchall()
    return [AttributedClaim(*r) for r in rows]


def count_sources_for_entity(conn: sqlite3.Connection, entity_id: str) -> int:
    """num_sources(E): the count of DISTINCT sources that make a claim about
    `entity_id` — the derived attribution breadth (not stored). Two claims from
    one source count once; the value gates page-worthiness and renders as the
    page's `num_sources`."""
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT c.source_id)
        FROM claim_entities ce
        JOIN claims c ON c.claim_id = ce.claim_id
        WHERE ce.entity_id = ?
        """,
        (entity_id,),
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def has_derived_for_entity(conn: sqlite3.Connection, entity_id: str) -> bool:
    """Whether `entity_id` carries any `derived` claim (a promoted note) — the
    signal a page holds the user's own synthesis, surfaced in resolve.json."""
    row = conn.execute(
        """
        SELECT 1
        FROM claim_entities ce
        JOIN claims c ON c.claim_id = ce.claim_id
        WHERE ce.entity_id = ? AND c.claim_kind = 'derived'
        LIMIT 1
        """,
        (entity_id,),
    ).fetchone()
    return row is not None


def _yaml_scalar(value: str) -> str:
    """Format a scalar string for inline YAML frontmatter (correct quoting via a
    round-trip through yaml.dump), mirroring domains.wiki.io."""
    dumped = yaml.dump({"_": value}, default_flow_style=False, sort_keys=False).rstrip()
    return dumped[len("_: ") :]


def _yaml_inline_list(items: list[str]) -> str:
    """Format a list of strings in inline `[a, b]` frontmatter form."""
    return yaml.dump(items, default_flow_style=True, sort_keys=False).rstrip()


def _source_domain(url: str | None) -> str | None:
    """The `www`-stripped netloc of a source URL — the publication proxy this
    corpus attributes with (queue.db has no publication field). None if no url."""
    if not url:
        return None
    netloc = urlparse(url).netloc
    return netloc[len("www.") :] if netloc.startswith("www.") else (netloc or None)


def _attribution(claim: AttributedClaim) -> str:
    """The attribution tail for one claim: `<who> · <domain> (<date>)`.

    `who` prefers an explicit publication, else the author; `domain` is the
    source URL's netloc. Missing parts drop out; a claim with no attributor at
    all renders `source unknown` so it never reads as an unsourced assertion."""
    who = claim.publication or claim.author
    domain = _source_domain(claim.url)
    # A source claim must link back to its origin — render the domain as a real
    # backlink `[domain](url)`, not bare text, so no claim reads as unsourced.
    link = f"[{domain}]({claim.url})" if domain and claim.url else domain
    left = " · ".join(part for part in (who, link) if part) or "source unknown"
    # Both dates are DISTINCT, explicitly labelled signals — a missing publish
    # date shows only the fetch date, never substituted into the publish slot.
    parts = []
    if claim.published_at:
        parts.append(f"published {claim.published_at}")
    if claim.fetched_at:
        parts.append(f"fetched {claim.fetched_at}")
    return f"{left} ({', '.join(parts)})" if parts else left


def _summary(claims: list[AttributedClaim]) -> str:
    """A deterministic one-line summary for the page — the lead claim's first
    line, preferring a reported (definitional) claim, then opinion, then a
    promoted note (markdown header markers stripped). Serves both the display
    line and the vector-lane embed text; a crude heuristic, an LLM summary is the
    deferred upgrade. Empty only if the page has no claims at all."""
    for kind in ("reported", "opinion", "derived"):
        for c in claims:
            if c.claim_kind != kind:
                continue
            lines = c.text.strip().splitlines()  # empty for blank/whitespace-only text
            if lines and (first_line := lines[0].lstrip("#").strip()):
                return first_line
    return ""


def _note_caption(claim: AttributedClaim) -> str:
    """The caption for a derived (promoted-note) block: `<note title> — my note,
    <date>`. The title names the user's own note and, when a note-file backlink is
    present, links to it — so a derived claim traces back to its origin note rather
    than reading as an unsourced assertion. Missing parts drop out."""
    title = claim.title or "Untitled note"
    label = f"[{title}]({claim.url})" if claim.url else title
    return (
        f"{label} — my note, {claim.published_at}" if claim.published_at else f"{label} — my note"
    )


def render_attributed_markdown(
    *,
    entity: EntityRecord,
    claims: list[AttributedClaim],
    aliases: list[str],
    num_sources: int,
    updated_at: str,
    related: Sequence[str] = (),
) -> str:
    """Render an entity's attributed page to markdown — YAML frontmatter, then the
    claims split into `## Reported` / `## Opinion` sections (the header conveys the
    kind, so no inline tag), each a bullet with its attribution tail. Within a
    section claims keep their dated order from `attributed_claims_for_entity`; an
    empty section is omitted. `aliases`/`related`/`num_sources`/`updated_at` are
    producer-authoritative (derived from wiki.db at write time); `related` is the
    co-occurring entity names from `get_related_for_entity`."""
    frontmatter = [
        "---",
        f"entity_id: {_yaml_scalar(entity.entity_id)}",
        f"title: {_yaml_scalar(entity.canonical_name)}",
        f"entity_type: {_yaml_scalar(entity.entity_type)}",
        f"aliases: {_yaml_inline_list(aliases)}",
        f"related: {_yaml_inline_list(list(related))}",
        f"summary: {_yaml_scalar(_summary(claims))}",
        f"num_sources: {int(num_sources)}",
        f"updated_at: {updated_at}",
        "---",
    ]
    sections = []
    # Derived (promoted-note) claims lead: they are the user's own synthesis, a
    # structured artifact rendered as a verbatim block (not a one-line bullet),
    # each captioned with the note it came from (title + date) so the block reads
    # as attributed to the user's note, never "source unknown".
    derived_blocks = [
        f"*{_note_caption(c)}*\n\n{c.text}" for c in claims if c.claim_kind == "derived"
    ]
    if derived_blocks:
        sections.append("## From my notes\n\n" + "\n\n".join(derived_blocks))
    for kind, heading in (("reported", "Reported"), ("opinion", "Opinion")):
        bullets = [f"- {c.text} — {_attribution(c)}" for c in claims if c.claim_kind == kind]
        if bullets:
            sections.append(f"## {heading}\n\n" + "\n".join(bullets))
    body = f"# {entity.canonical_name}\n\n" + "\n\n".join(sections)
    return "\n".join(frontmatter) + "\n\n" + body + "\n"
