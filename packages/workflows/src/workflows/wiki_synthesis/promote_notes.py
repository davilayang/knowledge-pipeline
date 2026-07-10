"""promote_notes — attach user-promoted notes to canonical wiki entities as
`derived` claims (KP-2).

Each `promote: true` note becomes ONE `derived` claim on a note-origin source
(`content_key = local:{note_id}`), linked to every entity its `entities` hints
resolve to. Resolution reuses the wiki resolver (`resolve_or_mint_batch`):
exact-name + alias gates are authoritative (alias-aware, so a merged-away hint
lands on the survivor); a miss mints a new `concept` entity. All hints across
all notes resolve in ONE batch so two notes naming the same new concept collapse
to one entity. Idempotent + reconciling: each tick REPLACES a note's claim and
removes claims for notes no longer promoted.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from domains.notes.promoted import PromotedNote, read_promoted_notes
from domains.wiki.attributed import (
    ClaimRecord,
    SourceRecord,
    claim_text_hash,
    delete_claims_for_source,
    delete_source,
    get_claims_for_source,
    get_entities_for_claim,
    get_source_keys_by_origin,
    insert_claim,
    insert_claim_entity,
    mint_claim_id,
    mint_source_id,
    upsert_source,
)
from domains.wiki.identity import Candidate, normalize_name, resolve_or_mint_batch
from domains.wiki.state import build_entity_index, connection, get_rejected, insert_entity


@dataclass
class PromoteResult:
    written: int = 0  # promoted notes written this tick (total)
    changed: int = 0  # of those, new-or-body-changed (an unchanged re-promote is not counted)
    removed: int = 0  # note-sources reconciled away (unpromoted / deleted)
    fuzzy_hints: list[tuple[str, str]] = field(default_factory=list)  # advisory near-miss log

    @property
    def dirty(self) -> int:
        """Non-zero iff wiki.db changed this tick — the render trigger. An
        unchanged standing note must NOT force a re-render (it would churn every
        page's updated_at + the curation push, the invariant render_pages keeps)."""
        return self.changed + self.removed


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def promote_notes(*, db_path: Path | str, notes_dir: Path | str) -> PromoteResult:
    notes = read_promoted_notes(Path(notes_dir))
    now = _now_iso()

    with connection(db_path) as conn:
        index = build_entity_index(conn)
        # H1 — curator denylist: drop any hint whose name is tombstoned in
        # rejected_entities BEFORE resolution. resolve_or_mint_batch does not gate
        # the denylist, so without this a rejected name re-mints and re-earns a page.
        rejected = {r.normalized_name for r in get_rejected(conn)}
        # Every surviving hint across every note resolves in ONE batch —
        # resolve_or_mint_batch dedupes within the batch, so two notes naming the
        # same new concept mint it once (not one entity per note).
        pairs: list[tuple[PromotedNote, str]] = [
            (note, hint)
            for note in notes
            for hint in note.entities
            if normalize_name(hint) not in rejected
        ]
        candidates = [Candidate(name=hint, entity_type="concept") for _, hint in pairs]
        resolution = resolve_or_mint_batch(index, candidates, now=now)

        note_entity_ids: dict[str, list[str]] = defaultdict(list)
        for (note, _), resolved in zip(pairs, resolution.resolved, strict=True):
            note_entity_ids[note.note_id].append(resolved.entity_id)

        live_keys = {f"local:{note.note_id}" for note in notes}
        changed = 0
        removed = 0
        with conn:
            for entity in resolution.new_entities:
                insert_entity(conn, entity)
            for note in notes:
                changed += _write_note_claim(conn, note, note_entity_ids[note.note_id], now)
            # Reconcile removals: a note-origin source whose note is no longer
            # promoted (toggle off or file deleted) is stale — drop it (CASCADE
            # prunes its derived claim + claim_entities). Keyed by content_key, so a
            # note that flips back on re-mints the same deterministic rows.
            for content_key in get_source_keys_by_origin(conn, "note"):
                if content_key not in live_keys:
                    delete_source(conn, mint_source_id(content_key))
                    removed += 1

    return PromoteResult(
        written=len(notes), changed=changed, removed=removed, fuzzy_hints=resolution.fuzzy_hints
    )


def _write_note_claim(conn, note: PromotedNote, entity_ids: list[str], now: str) -> int:
    """Write a note as a note-origin source + one derived claim linked to each
    resolved entity. REPLACE semantics: the source's prior claim is deleted first,
    so a re-promote of an edited note reflects only the current body (and an
    unchanged body re-inserts the same deterministic rows — idempotent).

    Returns 1 if this note changed wiki.db (new, edited body, OR relinked to
    different entities), 0 if the stored derived claim + its links are identical
    (no render churn). The link set is part of the check because a hint-only edit
    (same body, different entities) shifts page-worthiness counts on both entities
    and so must trigger a re-render."""
    content_key = f"local:{note.note_id}"
    source_id = mint_source_id(content_key)
    text_hash = claim_text_hash(note.body)
    new_links = set(dict.fromkeys(entity_ids))
    prior = get_claims_for_source(conn, source_id)
    unchanged = (
        len(prior) == 1
        and prior[0].text_hash == text_hash
        and get_entities_for_claim(conn, prior[0].claim_id) == new_links
    )
    source = SourceRecord(
        source_id=source_id,
        content_key=content_key,
        origin_type="note",
        title=note.title,
        author=None,  # session_id is recoverable from the note file; not smuggled into author
        publication=None,
        url=None,
        published_at=note.date.isoformat() if note.date else None,
        content_hash=None,
        fetched_at=note.updated_at,  # freshness signal
        added_at=now,
    )
    upsert_source(conn, source)
    delete_claims_for_source(conn, source_id)

    claim_id = insert_claim(
        conn,
        ClaimRecord(
            claim_id=mint_claim_id(source_id, text_hash),
            source_id=source_id,
            text=note.body,
            text_hash=text_hash,
            claim_kind="derived",
            created_at=now,
        ),
    )
    for entity_id in new_links:  # deduped; relevance order isn't stored (recoverable from the note)
        insert_claim_entity(conn, claim_id=claim_id, entity_id=entity_id)
    return 0 if unchanged else 1
