"""Surrogate entity identity — minting, normalisation, and the resolve_or_mint
batch resolver.

The id is an opaque surrogate (`e_<16hex>`) minted ONCE by the system, never
recomputed — name-independent so it survives renames. Identity is decided by
the alias gate, not by the LLM:

  - exact `normalized_name` (or exact normalized alias) is AUTHORITATIVE → reuse.
  - a validated `matched_id` from the LLM (semantic match, e.g. MCP ↔ Model
    Context Protocol) → reuse, only if it points at an entity in the snapshot.
  - fuzzy similarity is ADVISORY ONLY — it never auto-merges a durable id
    (false merges are destructive); it records a hint for the curated merge and
    mints a fresh entity (prefer a safe false-split).

`resolve_or_mint_batch` is a pure function over an in-memory `EntityIndex`
snapshot; it stages new entities for the caller to INSERT inside the atomic
persist transaction, and attaches each resolved candidate's alias display forms
to its `ResolvedEntity` (so the caller registers aliases only for candidates
that survive the denylist). It dedupes within the batch — on normalised name and
on freshly-staged aliases — so two extractions that normalise to the same name,
or to a just-minted entity's alias, collapse to one entity before any write.
"""

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field

from domains.wiki.aliases import AliasEntry, AliasStore
from domains.wiki.types import WikiPage


def page_content_hash(page: WikiPage) -> str:
    """Stable hash of a page's *prose* — gates version appends (#47).

    Covers `{summary, content}` only. Everything else on a WikiPage is per-item
    volatile or tracked authoritatively elsewhere, so it must NOT fork an
    edition: `updated_at` is a timestamp; `sources` defaults to the single
    triggering item id (accumulated provenance lives in the page_sources ledger
    / num_sources); `related` is the triggering article's co-extracted siblings,
    not the entity's accumulated link state; `title`/`page_type` live on
    `entities`. An "edition" means the synthesised prose or summary changed.
    """
    payload = json.dumps(
        {"summary": page.summary, "content": page.content},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_name(name: str) -> str:
    """Match key: lowercased, trimmed, internal whitespace collapsed."""
    return " ".join(name.strip().lower().split())


def slugify(name: str) -> str:
    """System-generated slug: lowercase, non-alphanumeric runs → single '_'."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "entity"


def mint_surrogate() -> str:
    """A fresh opaque surrogate id: `e_` + 16 lowercase hex (64 bits)."""
    return "e_" + uuid.uuid4().hex[:16]


def shortid(entity_id: str) -> str:
    """First 8 hex of the surrogate — the human-facing filename suffix."""
    return entity_id.removeprefix("e_")[:8]


@dataclass(frozen=True)
class EntityRecord:
    entity_id: str
    canonical_name: str
    normalized_name: str
    slug: str
    page_type: str
    created_at: str


@dataclass
class Candidate:
    """An extracted entity proposed by the LLM (it never supplies a surrogate)."""

    name: str
    page_type: str
    matched_id: str | None = None
    aliases: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResolvedEntity:
    entity_id: str
    is_new: bool  # True iff this candidate minted a new entity in this batch
    aliases: tuple[str, ...] = ()  # alias display forms to register for this entity


@dataclass
class EntityIndex:
    """In-memory snapshot of existing identity, for read-only resolution."""

    by_normalized_name: dict[str, str]
    by_normalized_alias: dict[str, str]
    alias_store: AliasStore  # fuzzy matching — ADVISORY only

    @classmethod
    def build(cls, entities: list[EntityRecord], aliases: list[tuple[str, str]]) -> "EntityIndex":
        by_name = {e.normalized_name: e.entity_id for e in entities}
        by_alias = {normalize_name(a): eid for a, eid in aliases}
        entries: dict[str, AliasEntry] = {
            e.entity_id: AliasEntry(canonical=e.canonical_name, aliases=[]) for e in entities
        }
        for alias, eid in aliases:
            if eid in entries:
                entries[eid].aliases.append(alias)
        return cls(
            by_normalized_name=by_name,
            by_normalized_alias=by_alias,
            alias_store=AliasStore(entries=entries),
        )


@dataclass
class BatchResolution:
    resolved: list[ResolvedEntity]
    new_entities: list[EntityRecord]
    fuzzy_hints: list[tuple[str, str]]  # (candidate_name, suggested_entity_id) — advisory


def resolve_or_mint_batch(
    index: EntityIndex,
    candidates: list[Candidate],
    *,
    now: str,
    mint=mint_surrogate,
) -> BatchResolution:
    """Resolve each candidate to a surrogate id (reuse) or mint a new one.

    Pure: reads `index`, returns the resolution plus staged new entities for the
    caller to persist; each ResolvedEntity carries the alias display forms to
    register for it. Gate order — exact normalized_name and exact alias are
    AUTHORITATIVE (they beat the LLM's matched_id); matched_id is accepted only
    if it points at a known entity; fuzzy is advisory (recorded, never merged).
    Dedupes within the batch on both name and freshly-staged aliases.
    """
    by_name = dict(index.by_normalized_name)
    by_alias = dict(index.by_normalized_alias)
    known_ids = set(by_name.values()) | set(by_alias.values())

    resolved: list[ResolvedEntity] = []
    new_entities: list[EntityRecord] = []
    fuzzy_hints: list[tuple[str, str]] = []

    def register_aliases(displays: tuple[str, ...], eid: str) -> tuple[str, ...]:
        # Returns the aliases SAFE to persist for `eid` — never one that shadows
        # a different entity's canonical (by_name) or another entity's alias
        # (by_alias). Persisting a shadowing alias would create an aliases_index
        # collision (an alias equal to a different entity's canonical name). An
        # alias equal to this entity's own canonical is dropped as redundant.
        kept: list[str] = []
        for display in displays:
            norm = normalize_name(display)
            if not norm:
                continue
            if norm in by_name:
                # Shadows a canonical — drop (whether another entity's or own).
                continue
            if norm in by_alias:
                # Already claimed: keep only if it's this entity's (idempotent).
                if by_alias[norm] == eid:
                    kept.append(display)
                continue
            by_alias[norm] = eid
            kept.append(display)
        return tuple(kept)

    for cand in candidates:
        norm = normalize_name(cand.name)

        # 1. exact normalized_name — AUTHORITATIVE (beats the LLM's matched_id).
        if norm in by_name:
            eid, is_new, aliases = by_name[norm], False, tuple(cand.aliases)
        # 2. exact normalized alias — AUTHORITATIVE.
        elif norm in by_alias:
            eid, is_new, aliases = by_alias[norm], False, tuple(cand.aliases)
        # 3. LLM semantic match — only if it points at a known entity; stage the
        #    surface form too so the gate catches it deterministically next time.
        elif cand.matched_id and cand.matched_id in known_ids:
            eid, is_new, aliases = cand.matched_id, False, (cand.name, *cand.aliases)
        else:
            # 4. fuzzy — ADVISORY only; record a hint, then mint (safe false-split).
            hint = index.alias_store.fuzzy_match(cand.name)
            if hint:
                fuzzy_hints.append((cand.name, hint))
            # 5. mint a fresh surrogate.
            eid = mint()
            new_entities.append(
                EntityRecord(
                    entity_id=eid,
                    canonical_name=cand.name,
                    normalized_name=norm,
                    slug=slugify(cand.name),
                    page_type=cand.page_type,
                    created_at=now,
                )
            )
            by_name[norm] = eid
            known_ids.add(eid)
            is_new, aliases = True, tuple(cand.aliases)

        kept_aliases = register_aliases(aliases, eid)
        resolved.append(ResolvedEntity(entity_id=eid, is_new=is_new, aliases=kept_aliases))

    return BatchResolution(resolved=resolved, new_entities=new_entities, fuzzy_hints=fuzzy_hints)
