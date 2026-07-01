"""Attributed-lane entity assignment (Layer 1.5 → wiki, Slice 2).

Assigns each `SourceClaim` in a per-source claim set to the entity/entities it is
about. Runs downstream of and isolated from the claim extractor (which stays a
pure [reported]/[opinion] tagger): the summary's claims are the input, and the
output is a per-claim → entity-id mapping resolved against the LIVE wiki, so a
claim the attributed lane surfaces unifies with the entity the raw-article
synthesis path already minted rather than minting a duplicate.

The deterministic surface-form match (which candidate names appear in a claim) is
a HINT, not the answer:

  1. a claim naming exactly ONE entity is unambiguous — assigned it directly, no
     LLM cost.
  2. ambiguous claims — zero mentions (pronoun / implicit subject), ≥2 mentions
     (a possible passing co-mention), or one mention inside contrast/dependency
     phrasing (where the lone mention is often the object, e.g. "shift away from
     OpenAI") — go to ONE closed subject-attribution call over the whole claim
     list, which returns each claim's true subject(s) from the candidate set:
     demoting a mentioned non-subject ("Microsoft ditches OpenAI" is about
     Microsoft, not OpenAI) or resolving a pronoun the match missed. Injected, so
     the wiring is testable without an LLM.

Flow: article-grounded candidates (from `extract_entities`) → resolve_or_mint
against the LIVE wiki → per-claim match_claim (hint) → closed attribute_subjects
over the ambiguous claims → group_by_entity into per-entity attributed claim sets
(salient vs co-mention). See this package's README.md ("Attributed lane") for the
flow diagram and where this sits beside the raw-article path.

Persists nothing: like the Slice 1 gate diagnostic, this computes the mapping;
the storage schema is deferred until page synthesis (Slice 3) fixes its shape.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from domains.wiki.claims import ClaimSet, SourceClaim, parse_claims_doc
from domains.wiki.identity import (
    Candidate,
    EntityRecord,
    normalize_name,
    resolve_or_mint_batch,
)
from domains.wiki.salience import count_mentions, is_salient, salience_features
from domains.wiki.state import (
    build_entity_index,
    connection,
    get_aliases_for_entity,
    get_entity,
)
from pydantic import BaseModel, Field

from workflows.llm import generate_structured_with_usage
from workflows.wiki_synthesis.extract_entities import parse_entity_candidates
from workflows.wiki_synthesis.prompts import (
    SUBJECT_ATTRIBUTION_SYSTEM,
    SUBJECT_ATTRIBUTION_USER,
)

SUBJECT_MODEL = "gpt-4.1-mini"

# Contrast / dependency phrasing where the single named entity is usually the
# OBJECT (moved away from, compared against, depended on), not the subject — e.g.
# "shift workloads away from OpenAI". A one-mention claim carrying one of these is
# treated as ambiguous and routed to subject-attribution instead of trusting the
# lone mention.
_CONTRAST_CUE = re.compile(
    r"\b(?:unlike|instead of|rather than|away from|compared with|compared to|"
    r"depends on|dependence on|dependent on|reliant on|reliance on|versus|vs\.?)\b",
    re.IGNORECASE,
)

# attribute_subjects: (claim_texts, per-claim mention hints, candidate_names) →
# per-claim SUBJECT names. One closed LLM call over the whole claim list decides
# which candidate(s) each ambiguous claim is ABOUT (demoting passing co-mentions,
# resolving pronouns). Injected + name-based, so id resolution stays deterministic
# and the wiring is testable with a fake.
SubjectMapper = Callable[[list[str], list[list[str]], list[str]], list[list[str]]]


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
    shared deterministic salience gate over the claim texts) — the rest are
    passing co-mentions.

    Carries no LLM-cost field: candidate extraction happens upstream (the
    extract_entities asset), so its cost is accounted there; the subject-
    attribution call's cost is captured by the synthesis asset (3c) that owns the
    per-source cost roll-up, not by this pure-computation step."""

    item_id: str
    assignments: tuple[ClaimAssignment, ...]
    entities: dict[str, EntityRecord]
    new_entities: tuple[EntityRecord, ...]
    salient_entity_ids: frozenset[str] = frozenset()


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
    summary: ClaimSet,
    candidates: list[Candidate],
    *,
    db_path,
    attribute_subjects: SubjectMapper | None = None,
) -> SummaryAssignment:
    """Assign each claim in `summary` to the entity/entities it is about.

    `candidates` are the article-grounded entities produced upstream by
    `extract_entities(article, claims)` (the `extract_entities` asset) — reading
    the raw article, not the claims, so the article's implicit subject and
    long-tail are present. This step is pure resolution + attribution: the
    candidates are resolved against the LIVE wiki via the same
    `resolve_or_mint_batch` the raw-article path uses, so a candidate naming an
    existing entity unifies onto its surrogate id instead of minting a parallel one.

    Each claim's mentioned entities (deterministic surface-form match) are a HINT,
    not the answer. A claim naming exactly one entity is unambiguous — assigned it
    directly, no LLM. Ambiguous claims (zero mentions = pronoun/implicit subject;
    ≥2 mentions = a possible passing co-mention) go to one closed subject-
    attribution call over the whole claim list, which returns each claim's true
    subject(s) from the candidate set — demoting a mentioned non-subject, or
    resolving a pronoun the match missed.
    """
    if attribute_subjects is None:
        attribute_subjects = attribute_subjects_llm

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

    # Deterministic mentions per claim — the hint. An exactly-one-mention claim is
    # unambiguous and keeps its match; zero-or-≥2-mention claims are ambiguous.
    mention_ids = [match_claim(claim.text, surface_forms) for claim in summary.claims]
    per_claim_ids = [list(ids) for ids in mention_ids]

    # Ambiguous = zero mentions (pronoun/implicit), ≥2 mentions (possible passing
    # co-mention), OR exactly one mention inside contrast/dependency phrasing where
    # that lone mention is often the object, not the subject.
    ambiguous_idx = [
        i
        for i, ids in enumerate(mention_ids)
        if len(ids) != 1 or _CONTRAST_CUE.search(summary.claims[i].text)
    ]
    if ambiguous_idx:
        name_to_id = {
            normalize_name(form): eid for eid, forms in surface_forms.items() for form in forms
        }
        # The whole claim list goes to the mapper (so pronouns have discourse
        # context) with each claim's mention hint; only ambiguous claims take the
        # mapper's verdict. Names outside the candidate set are dropped (closed).
        claim_texts = [c.text for c in summary.claims]
        hints = [[entities[e].canonical_name for e in ids] for ids in mention_ids]
        candidate_names = [entities[eid].canonical_name for eid in entities]
        subjects = attribute_subjects(claim_texts, hints, candidate_names)
        for i in ambiguous_idx:
            names = subjects[i] if i < len(subjects) else []
            ids = []
            for name in names:
                eid = name_to_id.get(normalize_name(name))
                if eid and eid not in ids:
                    ids.append(eid)
            per_claim_ids[i] = ids

    assignments = tuple(
        ClaimAssignment(claim=claim, entity_ids=tuple(ids))
        for claim, ids in zip(summary.claims, per_claim_ids, strict=True)
    )

    # Salience over the pooled claim texts, via the SAME deterministic gate the
    # raw-article path uses — an entity central to this source clears the mention
    # floor; a one-off co-mention doesn't. Uses the claim texts (not the rendered
    # doc): cleaner than the prior body, whose frontmatter `item_id` (a source URL)
    # could have inflated a mention count when the URL itself contained a name.
    body = "\n".join(c.text for c in summary.claims)
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
    )


def assign_from_stored(
    claims_doc: str,
    candidates_doc: str,
    *,
    db_path,
    attribute_subjects: SubjectMapper | None = None,
) -> SummaryAssignment:
    """Assign a source's claims from its STORED extract-time outputs — the rendered
    claims doc (`get_claims`) and the rendered candidate list (`get_candidates`),
    both from the queue store. Parses each and runs `assign_summary`. The bridge the
    synthesis-side consumer uses to turn per-source extract_entities output into an
    assignment (then `group_by_entity` into `EntityClaims`).

    Source coherence is the caller's invariant: read both docs by the SAME page_id
    (`get_claims(pid)` + `get_candidates(pid)`) so the claims and candidates belong
    to one source — the candidate format carries no id to cross-check here. A
    malformed candidate doc parses to no candidates (fail-soft: an empty assignment,
    not an error), mirroring how the producer treats an unparseable extraction."""
    return assign_summary(
        parse_claims_doc(claims_doc),
        parse_entity_candidates(candidates_doc),
        db_path=db_path,
        attribute_subjects=attribute_subjects,
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


class _ClaimSubject(BaseModel):
    claim_index: int = Field(description="0-based index of the claim in the input list")
    subject_names: list[str] = Field(
        default_factory=list,
        description="Candidate entity names this claim is ABOUT — its subject(s), a "
        "subset of the provided candidates; empty when about none of them.",
    )


class _SubjectMapping(BaseModel):
    subjects: list[_ClaimSubject] = Field(default_factory=list)


def attribute_subjects_llm(
    claim_texts: list[str],
    mention_hints: list[list[str]],
    candidate_names: list[str],
) -> list[list[str]]:
    """Production `SubjectMapper`: one closed structured call returning each
    claim's subject(s) from the candidate set.

    The whole claim list is sent together (so pronouns / implicit subjects have
    the surrounding claims as context), each annotated with its mention hint.
    Returns a per-claim list in INPUT order — the model may answer out of order or
    omit a claim, so the response is re-indexed by `claim_index` (missing → empty).
    Short-circuits (no LLM call) when there are no claims or no candidates."""
    if not claim_texts or not candidate_names:
        return [[] for _ in claim_texts]
    numbered = "\n".join(
        f"{i}. {t}   [mentions: {', '.join(mention_hints[i]) or 'none'}]"
        for i, t in enumerate(claim_texts)
    )
    candidate_block = "\n".join(f"- {n}" for n in candidate_names)
    user = SUBJECT_ATTRIBUTION_USER.format(candidates=candidate_block, claims=numbered)
    result, _call = generate_structured_with_usage(
        user,
        schema=_SubjectMapping,
        system=SUBJECT_ATTRIBUTION_SYSTEM,
        model=SUBJECT_MODEL,
    )
    by_index = {s.claim_index: list(s.subject_names) for s in result.subjects}
    return [by_index.get(i, []) for i in range(len(claim_texts))]
