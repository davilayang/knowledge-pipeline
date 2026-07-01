"""Attributed-lane entity assignment (Layer 1.5 → wiki, Slice 2).

Assigns each `SourceClaim` in a per-source summary to the entity/entities it is
about. Runs downstream of and isolated from the source summariser (which stays a
pure [reported]/[opinion] tagger): the summary's claims are the input, and the
output is a per-claim → entity-id mapping resolved against the LIVE wiki, so a
claim the attributed lane surfaces unifies with the entity the raw-article
synthesis path already minted rather than minting a duplicate.

Two levels, mirroring the design's cost/precision split:

  1. deterministic surface-form match — a claim that names a resolved entity by
     canonical name or alias (word-boundary) is assigned it, no LLM cost.
  2. a bounded LLM residual mapper for the claims the match misses (pronoun /
     implicit-subject / multi-entity claims) — injected, so the deterministic
     wiring is testable without an LLM.

Flow (per source summary):

    SourceSummary (tagged claims)
        │  render_source_summary → extract() over the claims (LLM)
        ▼
    candidates ──resolve_or_mint_batch (LIVE wiki)──► entities + surface_forms
        │            reuse an existing surrogate, else mint    (cross-path unification)
        ▼
    per claim:  match_claim  (deterministic surface-form)
        │
        ├─ matched ──────────────────────────────────────► entity_ids
        └─ residual (no match) ──map_residual (LLM)────────► entity_ids
        ▼
    ClaimAssignment[]  +  salience over the body (shared gate)
        │  group_by_entity
        ▼
    EntityClaims[]  — per-entity attributed claim sets (salient vs co-mention)

Persists nothing: like the Slice 1 gate diagnostic, this computes the mapping;
the storage schema is deferred until page synthesis (Slice 3) fixes its shape.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from domains.types import IngestItem
from domains.wiki.identity import EntityRecord, normalize_name, resolve_or_mint_batch
from domains.wiki.salience import count_mentions, is_salient, salience_features
from domains.wiki.source_summary import SourceClaim, SourceSummary, render_source_summary
from domains.wiki.state import (
    build_entity_index,
    connection,
    get_aliases_for_entity,
    get_entity,
)
from pydantic import BaseModel, Field

from workflows.llm import LLMCall, generate_structured_with_usage
from workflows.wiki_synthesis.prompts import (
    RESIDUAL_ENTITY_MAP_SYSTEM,
    RESIDUAL_ENTITY_MAP_USER,
)
from workflows.wiki_synthesis.synthesize import extract

RESIDUAL_MODEL = "gpt-4.1-mini"

# extract_fn: run entity extraction over the summary body → {"candidates", "llm_calls"}.
# Injected so the assignment wiring is testable without the extraction LLM.
ExtractFn = Callable[[IngestItem], dict]

# map_residual: (residual_claim_texts, candidate_entity_names) → per-claim entity
# names. The bounded LLM step that resolves claims the deterministic match misses
# (pronoun / implicit-subject). Injected + name-based (never surrogate ids), so
# the id resolution stays deterministic and the wiring is testable with a fake.
ResidualMapper = Callable[[list[str], list[str]], list[list[str]]]


@dataclass(frozen=True)
class ClaimAssignment:
    """One claim paired with the entity ids it is about (empty = unassigned)."""

    claim: SourceClaim
    entity_ids: tuple[str, ...]


@dataclass(frozen=True)
class SummaryAssignment:
    """The whole per-source assignment: every claim mapped to entity ids, the
    entities involved (resolved against the live wiki, keyed by surrogate id),
    and the entities minted this run (staged for a later persist, not written
    here). `salient_entity_ids` are the entities central to this source (by the
    shared deterministic salience gate over the summary body) — the rest are
    passing co-mentions. `llm_calls` carries the extraction call for cost."""

    item_id: str
    assignments: tuple[ClaimAssignment, ...]
    entities: dict[str, EntityRecord]
    new_entities: tuple[EntityRecord, ...]
    salient_entity_ids: frozenset[str] = frozenset()
    llm_calls: list[LLMCall] = field(default_factory=list)


@dataclass(frozen=True)
class EntityClaims:
    """One entity with the claims attributed to it in a source — the per-entity
    attributed claim set Slice 3 synthesises a page from. `salient` is False for
    a passing co-mention (kept for the record, skipped by page synthesis)."""

    entity: EntityRecord
    claims: tuple[SourceClaim, ...]
    salient: bool


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def assign_summary(
    summary: SourceSummary,
    *,
    db_path,
    extract_fn: ExtractFn | None = None,
    map_residual: ResidualMapper | None = None,
) -> SummaryAssignment:
    """Assign each claim in `summary` to the entity/entities it is about.

    Extraction runs over the rendered summary body (the claims, not the raw
    article); the extracted entities are resolved against the LIVE wiki via the
    same `resolve_or_mint_batch` the raw-article path uses, so a claim naming an
    existing entity unifies onto its surrogate id instead of minting a parallel
    one. Each claim is then matched to those entities by surface form.
    """
    if extract_fn is None:
        extract_fn = lambda item: extract(item, db_path=db_path)  # noqa: E731
    if map_residual is None:
        map_residual = map_residual_llm

    body = render_source_summary(summary)
    item = IngestItem(
        item_id=summary.item_id,
        title="",
        date=None,
        text=body,
        source_type="queue",
        source_ref=summary.item_id,
    )
    ext = extract_fn(item)
    candidates = ext["candidates"]
    llm_calls = list(ext.get("llm_calls", []))

    with connection(db_path) as conn:
        index = build_entity_index(conn)
        resolution = resolve_or_mint_batch(index, candidates, now=_now_iso())
        new_by_id = {e.entity_id: e for e in resolution.new_entities}
        entities: dict[str, EntityRecord] = {}
        form_sets: dict[str, set[str]] = {}
        for cand, resolved in zip(candidates, resolution.resolved, strict=True):
            eid = resolved.entity_id
            rec = new_by_id.get(eid) or get_entity(conn, eid)
            if rec is None:
                continue
            entities[eid] = rec
            # Only RESOLVER-APPROVED surface forms: the stored canonical, the
            # aliases resolve_or_mint kept, and the entity's committed aliases.
            # Raw candidate aliases are NOT filtered for shadowing another entity's
            # canonical, so feeding them into name_to_id could misresolve a residual
            # name onto the wrong entity. Accumulate across candidates that resolve
            # to the SAME surrogate — last-write-wins would drop earlier variants
            # and hurt both surface matching and residual name resolution.
            forms = form_sets.setdefault(eid, set())
            forms.add(rec.canonical_name)
            forms.update(resolved.aliases)
            if not resolved.is_new:
                forms.update(get_aliases_for_entity(conn, eid))
        surface_forms = {
            eid: sorted(f for f in forms if f.strip()) for eid, forms in form_sets.items()
        }

    per_claim_ids = [match_claim(claim.text, surface_forms) for claim in summary.claims]

    # Residual pass: claims the surface-form match left empty (implicit / pronoun
    # subjects) go to the bounded LLM mapper, which answers in entity NAMES; those
    # are resolved back to surrogate ids deterministically (unknown names dropped).
    residual_idx = [i for i, ids in enumerate(per_claim_ids) if not ids]
    if residual_idx:
        name_to_id = {
            normalize_name(form): eid for eid, forms in surface_forms.items() for form in forms
        }
        residual_texts = [summary.claims[i].text for i in residual_idx]
        candidate_names = [entities[eid].canonical_name for eid in entities]
        mapped = map_residual(residual_texts, candidate_names)
        for i, names in zip(residual_idx, mapped, strict=True):
            ids: list[str] = []
            for name in names:
                eid = name_to_id.get(normalize_name(name))
                if eid and eid not in ids:
                    ids.append(eid)
            per_claim_ids[i] = ids

    assignments = tuple(
        ClaimAssignment(claim=claim, entity_ids=tuple(ids))
        for claim, ids in zip(summary.claims, per_claim_ids, strict=True)
    )

    # Salience over the summary body (the pooled claims), via the SAME
    # deterministic gate the raw-article path uses — an entity central to this
    # source clears the mention floor; a one-off co-mention doesn't.
    salient_entity_ids = frozenset(
        eid
        for eid, rec in entities.items()
        if is_salient(
            salience_features(
                name=rec.canonical_name,
                aliases=[f for f in surface_forms.get(eid, []) if f != rec.canonical_name],
                title="",
                text=body,
            )
        )
    )
    return SummaryAssignment(
        item_id=summary.item_id,
        assignments=assignments,
        entities=entities,
        new_entities=tuple(resolution.new_entities),
        salient_entity_ids=salient_entity_ids,
        llm_calls=llm_calls,
    )


def group_by_entity(assignment: SummaryAssignment) -> list[EntityClaims]:
    """Invert claim→entity into per-entity attributed claim sets.

    One `EntityClaims` per entity that at least one claim was assigned to, each
    carrying its claims in document order and the summary's salience verdict.
    Ordered by entity id for determinism. Entities with no attributed claim
    (extracted but never matched to a claim) are omitted."""
    claims_by_entity: dict[str, list[SourceClaim]] = {}
    for ca in assignment.assignments:
        for eid in ca.entity_ids:
            claims_by_entity.setdefault(eid, []).append(ca.claim)
    return [
        EntityClaims(
            entity=assignment.entities[eid],
            claims=tuple(claims),
            salient=eid in assignment.salient_entity_ids,
        )
        for eid, claims in sorted(claims_by_entity.items())
    ]


def match_claim(text: str, surface_forms: dict[str, list[str]]) -> list[str]:
    """Entity ids whose canonical name or any alias appears in `text`.

    Word-boundary, case-insensitive (reuses the salience mention counter), so
    "OpenAI" hits an OpenAI entity but "category" never hits a "cat" entity. Ids
    are returned in `surface_forms` iteration order for determinism."""
    return [
        entity_id
        for entity_id, forms in surface_forms.items()
        if count_mentions(forms[0] if forms else "", forms[1:], text) > 0
    ]


class _ClaimEntities(BaseModel):
    claim_index: int = Field(description="0-based index of the claim in the input list")
    entity_names: list[str] = Field(
        default_factory=list,
        description="Candidate entity names this claim is ABOUT (a subset of the "
        "provided candidates); empty when none can be confidently resolved.",
    )


class _ResidualMapping(BaseModel):
    mappings: list[_ClaimEntities] = Field(default_factory=list)


def map_residual_llm(residual_texts: list[str], candidate_names: list[str]) -> list[list[str]]:
    """Production `ResidualMapper`: one structured LLM call mapping each residual
    claim to the candidate entities it is about.

    Returns a per-claim list in INPUT order — the model may answer out of order
    or omit a claim it couldn't resolve, so the response is re-indexed by
    `claim_index` and any missing index comes back as an empty list. Short-
    circuits (no LLM call) when there are no residual claims.

    FOR LATER: residual claims are sent as ISOLATED strings, stripped of the
    surrounding claims — so a pronoun / implicit subject ("It later expanded")
    has no discourse context to resolve against and the model must guess. The
    next iteration (subject-attribution over ALL claims in one call, not just
    residuals) passes the full ordered claim list and fixes this together with
    the mention-vs-subject over-attribution the deterministic match leaves."""
    if not residual_texts or not candidate_names:
        # No claims to map, or no entities to map them to → skip the LLM call.
        return [[] for _ in residual_texts]
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(residual_texts))
    candidate_block = "\n".join(f"- {n}" for n in candidate_names) or "(none)"
    user = RESIDUAL_ENTITY_MAP_USER.format(candidates=candidate_block, claims=numbered)
    result, _call = generate_structured_with_usage(
        user,
        schema=_ResidualMapping,
        system=RESIDUAL_ENTITY_MAP_SYSTEM,
        model=RESIDUAL_MODEL,
    )
    by_index = {m.claim_index: list(m.entity_names) for m in result.mappings}
    return [by_index.get(i, []) for i in range(len(residual_texts))]
