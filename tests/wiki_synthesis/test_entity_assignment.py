"""Attributed-lane entity assignment.

Assigns each SourceClaim to the entity/entities it is about, resolving the
article-grounded candidates (produced upstream by extract_entities) against the
LIVE wiki so an attributed-lane claim unifies with the entity the raw-article
synthesis path already minted (cross-path unification). Candidates are passed in
directly and the subject-attribution mapper is injected, so the wiring is driven
through the public surface with fakes.
"""

from datetime import UTC, datetime

from domains.wiki.claims import ClaimSet, SourceClaim
from domains.wiki.identity import Candidate, EntityRecord, normalize_name, slugify
from domains.wiki.state import connection, insert_entity
from workflows.wiki_synthesis.entity_assignment import (
    assign_from_stored,
    assign_summary,
    group_by_entity,
    match_claim,
)

from tests.wiki_synthesis._helpers import make_llm_call


def _seed_entity(db_path, name: str, *, page_type: str = "concept") -> str:
    """Insert an entity as the raw-article synthesis path would have, and return
    its surrogate id — the entity the attributed lane must unify onto."""
    entity_id = "e_seed0000000000"
    with connection(db_path) as conn:
        with conn:
            insert_entity(
                conn,
                EntityRecord(
                    entity_id=entity_id,
                    canonical_name=name,
                    normalized_name=normalize_name(name),
                    slug=slugify(name),
                    page_type=page_type,
                    created_at=datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )
    return entity_id


def _candidates(*names: str) -> list[Candidate]:
    """The article-grounded candidates assign_summary now consumes (produced
    upstream by extract_entities); plain Candidates, resolution decides reuse vs mint."""
    return [Candidate(name=n, page_type="concept") for n in names]


def _summary(item_id: str, *claims: SourceClaim) -> ClaimSet:
    return ClaimSet(item_id=item_id, content_date="2026-03-01", claims=list(claims))


def test_match_claim_hits_entity_by_surface_form():
    # A claim naming an entity's surface form resolves to that entity's id;
    # word-boundary, so "OpenAI" hits e_1 and nothing else.
    surface_forms = {"e_1": ["OpenAI"], "e_2": ["Anthropic"]}
    assert match_claim("OpenAI released GPT-5.", surface_forms) == ["e_1"]


def test_match_claim_hits_entity_by_alias():
    # An entity named by an alias ("MCP") is matched even when the claim never
    # uses its canonical form ("Model Context Protocol") — aliases are surface
    # forms too, so the attributed lane counts them like the salience gate does.
    surface_forms = {"e_1": ["Model Context Protocol", "MCP"]}
    assert match_claim("Anthropic shipped MCP support.", surface_forms) == ["e_1"]


def test_claim_unifies_onto_existing_wiki_entity(wiki_db_path):
    # The raw-article path already minted "OpenAI". A summary claim naming OpenAI
    # must resolve to that SAME surrogate id, not mint a parallel entity — this
    # is the cross-path unification the Slice 2.0 spike (empty db) never exercised.
    existing_id = _seed_entity(wiki_db_path, "OpenAI")
    summary = _summary(
        "https://medium.com/p/abc",
        SourceClaim(text="OpenAI released GPT-5.", source_id="https://medium.com/p/abc"),
    )

    result = assign_summary(summary, _candidates("OpenAI"), db_path=wiki_db_path)

    assert len(result.assignments) == 1
    assert result.assignments[0].entity_ids == (existing_id,)
    # Unified onto the existing entity → nothing new minted.
    assert result.new_entities == ()


def test_new_entity_is_minted_and_surfaced(wiki_db_path):
    # Empty wiki: a claim naming an entity nothing has seen mints a fresh
    # surrogate, surfaced in new_entities for a later persist (Slice 3 writes it).
    summary = _summary(
        "https://medium.com/p/xyz",
        SourceClaim(text="Anthropic shipped Claude Opus.", source_id="https://medium.com/p/xyz"),
    )

    result = assign_summary(summary, _candidates("Anthropic"), db_path=wiki_db_path)

    assert len(result.new_entities) == 1
    minted = result.new_entities[0]
    assert minted.canonical_name == "Anthropic"
    assert result.assignments[0].entity_ids == (minted.entity_id,)


def test_pronoun_claim_resolved_by_subject_mapper(wiki_db_path):
    # Claim 2 has an implicit subject ("It ...") — zero mentions, so it is
    # ambiguous and goes to the subject mapper, which resolves it to Anthropic
    # using the surrounding claims. Claim 1 (exactly one mention) is unambiguous
    # and kept deterministically. The mapper receives the FULL claim list + hints.
    sid = "https://medium.com/p/imp"
    summary = _summary(
        sid,
        SourceClaim(text="Anthropic is an AI lab.", source_id=sid),
        SourceClaim(text="It raised $2B in fresh funding.", source_id=sid),
    )
    seen = {}

    def subjects(texts, hints, candidates):
        seen["texts"] = list(texts)
        seen["hints"] = [list(h) for h in hints]
        seen["candidates"] = list(candidates)
        return [["Anthropic"], ["Anthropic"]]

    result = assign_summary(
        summary,
        _candidates("Anthropic"),
        db_path=wiki_db_path,
        attribute_subjects=subjects,
    )

    ent = result.new_entities[0].entity_id
    assert result.assignments[0].entity_ids == (ent,)  # 1 mention → deterministic
    assert result.assignments[1].entity_ids == (ent,)  # pronoun → subject-mapped
    assert seen["texts"] == ["Anthropic is an AI lab.", "It raised $2B in fresh funding."]
    assert seen["hints"] == [["Anthropic"], []]  # claim 2 carries no mention hint
    assert "Anthropic" in seen["candidates"]


def test_subject_name_not_in_candidates_is_dropped(wiki_db_path):
    # A subject mapper that returns a name outside the candidate set contributes
    # nothing — the ambiguous claim stays unassigned rather than inventing a link.
    sid = "https://medium.com/p/hallucinate"
    summary = _summary(
        sid,
        SourceClaim(text="Anthropic is an AI lab.", source_id=sid),
        SourceClaim(text="It later expanded overseas.", source_id=sid),
    )

    def hallucinating(texts, hints, candidates):
        return [["Anthropic"], ["Nonexistent Corp"]]

    result = assign_summary(
        summary,
        _candidates("Anthropic"),
        db_path=wiki_db_path,
        attribute_subjects=hallucinating,
    )

    assert result.assignments[1].entity_ids == ()


def test_group_by_entity_uses_subject_not_comention(wiki_db_path):
    # "Anthropic" is the subject of all 3 claims; claim 3 names OpenAI only as a
    # passing comparison. Subject-attribution demotes OpenAI (not the subject), so
    # it is assigned NO claim — group_by_entity yields only Anthropic.
    sid = "https://medium.com/p/grp"
    summary = _summary(
        sid,
        SourceClaim(text="Anthropic released Claude.", source_id=sid),
        SourceClaim(text="Anthropic raised fresh funding.", source_id=sid),
        SourceClaim(text="Anthropic, unlike OpenAI, focuses on safety.", source_id=sid),
    )

    def subjects(texts, hints, candidates):
        # Only claim 3 (2 mentions) is ambiguous; its subject is Anthropic.
        return [[], [], ["Anthropic"]]

    result = assign_summary(
        summary,
        _candidates("Anthropic", "OpenAI"),
        db_path=wiki_db_path,
        attribute_subjects=subjects,
    )
    groups = {g.entity.canonical_name: g for g in group_by_entity(result)}

    assert set(groups) == {"Anthropic"}
    assert len(groups["Anthropic"].claims) == 3


def test_subject_mapper_reassembles_by_index(monkeypatch):
    # The production subject mapper turns the structured LLM response back into a
    # per-claim list in INPUT order. The model may return indices out of order and
    # omit a claim — that claim must come back as an empty list.
    from workflows.wiki_synthesis import entity_assignment as ea

    def fake_structured(prompt, *, schema, system="", model=""):
        result = schema(
            subjects=[
                {"claim_index": 2, "subject_names": ["OpenAI"]},
                {"claim_index": 0, "subject_names": ["Anthropic", "Claude"]},
            ]
        )
        return result, make_llm_call()

    monkeypatch.setattr(ea, "generate_structured_with_usage", fake_structured)

    mapped = ea.attribute_subjects_llm(
        ["claim zero", "claim one", "claim two"], [[], [], []], ["Anthropic", "Claude", "OpenAI"]
    )

    assert mapped == [["Anthropic", "Claude"], [], ["OpenAI"]]


def test_subject_mapper_short_circuits(monkeypatch):
    # No claims, or no candidates to map to → no LLM call at all (cost guard).
    from workflows.wiki_synthesis import entity_assignment as ea

    def boom(*a, **k):
        raise AssertionError("LLM must not be called for empty inputs")

    monkeypatch.setattr(ea, "generate_structured_with_usage", boom)
    assert ea.attribute_subjects_llm([], [], ["Anthropic"]) == []
    assert ea.attribute_subjects_llm(["a claim"], [[]], []) == [[]]


def test_assign_persists_nothing(wiki_db_path):
    # The "persists nothing" invariant is load-bearing: assign_summary MINTS an
    # entity id but must NOT write it — Slice 3 owns persistence. The DB is empty
    # before and after; the minted entity lives only in new_entities.
    from domains.wiki.state import connection, get_all_entities

    summary = _summary(
        "https://medium.com/p/np",
        SourceClaim(text="Cohere shipped a new model.", source_id="https://medium.com/p/np"),
    )
    result = assign_summary(summary, _candidates("Cohere"), db_path=wiki_db_path)

    assert len(result.new_entities) == 1  # minted in memory
    with connection(wiki_db_path) as conn:
        assert get_all_entities(conn) == []  # but nothing written


def test_duplicate_candidates_to_same_surrogate_keep_all_surface_forms(wiki_db_path):
    # Two candidates that normalise to the same entity ("OpenAI" and "openai")
    # resolve to one surrogate; the accumulated surface forms must still match a
    # claim using either form (last-write-wins would drop one).
    existing_id = _seed_entity(wiki_db_path, "OpenAI")
    sid = "https://medium.com/p/dup"
    summary = _summary(sid, SourceClaim(text="openai shipped Sora.", source_id=sid))

    result = assign_summary(summary, _candidates("OpenAI", "openai"), db_path=wiki_db_path)

    assert result.assignments[0].entity_ids == (existing_id,)
    assert result.new_entities == ()


def test_subject_attribution_overrides_multi_mention(wiki_db_path):
    # A claim naming TWO entities is ambiguous; the injected subject mapper picks
    # Microsoft, so OpenAI (a passing co-mention) is NOT assigned. This is the
    # precision fix — mention-match would have attributed the claim to BOTH.
    ms = _seed_entity(wiki_db_path, "Microsoft")
    sid = "https://medium.com/p/ms"
    summary = _summary(sid, SourceClaim(text="Microsoft will ditch OpenAI models.", source_id=sid))

    def subjects(texts, hints, candidates):
        return [["Microsoft"]]

    result = assign_summary(
        summary,
        _candidates("Microsoft", "OpenAI"),
        db_path=wiki_db_path,
        attribute_subjects=subjects,
    )

    assert result.assignments[0].entity_ids == (ms,)


def test_single_mention_with_contrast_cue_is_ambiguous(wiki_db_path):
    # "away from OpenAI" names OpenAI once, but OpenAI is what's being moved away
    # FROM — not the subject. A contrast cue routes this single-mention claim to
    # the subject mapper (which demotes OpenAI → Microsoft), instead of trusting
    # the lone mention. Claim 1 (one mention, no cue) stays deterministic.
    ms = _seed_entity(wiki_db_path, "Microsoft")
    sid = "https://medium.com/p/shift"
    summary = _summary(
        sid,
        SourceClaim(text="Microsoft is a tech giant.", source_id=sid),
        SourceClaim(text="It will shift workloads away from OpenAI.", source_id=sid),
    )

    def subjects(texts, hints, candidates):
        return [[], ["Microsoft"]]

    result = assign_summary(
        summary,
        _candidates("Microsoft", "OpenAI"),
        db_path=wiki_db_path,
        attribute_subjects=subjects,
    )

    assert result.assignments[1].entity_ids == (ms,)


def test_assign_from_stored_parses_docs_and_assigns(wiki_db_path):
    # The storage bridge: the rendered claims doc + rendered candidate list (as the
    # queue store holds them) parse back and run assign_summary — the entry point
    # the synthesis-side consumer calls per source.
    from domains.wiki.claims import render_claims
    from workflows.wiki_synthesis.extract_entities import render_candidates

    existing_id = _seed_entity(wiki_db_path, "OpenAI")
    sid = "https://medium.com/p/stored"
    summary = _summary(sid, SourceClaim(text="OpenAI released GPT-5.", source_id=sid))

    result = assign_from_stored(
        render_claims(summary),
        render_candidates(_candidates("OpenAI")),
        db_path=wiki_db_path,
    )

    assert result.item_id == sid
    assert result.assignments[0].entity_ids == (existing_id,)
    assert result.new_entities == ()


def test_assign_from_stored_empty_candidates_yields_no_assignment(wiki_db_path):
    # A stored candidate doc with no candidates (source had no entities, or NONE)
    # parses to []; assignment proceeds with nothing to resolve — a valid empty
    # outcome, not an error. The bridge fails soft on empty candidates by design.
    from domains.wiki.claims import render_claims

    sid = "https://medium.com/p/empty"
    summary = _summary(sid, SourceClaim(text="A claim about nothing nameable.", source_id=sid))

    result = assign_from_stored(render_claims(summary), "NONE", db_path=wiki_db_path)

    assert result.new_entities == ()
    assert result.assignments[0].entity_ids == ()
