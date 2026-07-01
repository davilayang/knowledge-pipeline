"""Slice 2 — attributed-lane entity assignment.

Assigns each SourceClaim to the entity/entities it is about, resolving mentions
against the LIVE wiki so an attributed-lane claim unifies with the entity the
raw-article synthesis path already minted (cross-path unification). The LLM
boundaries (entity extraction over the claims, residual claim→entity mapping)
are injected so the wiring is driven through the public surface with fakes.
"""

from datetime import UTC, datetime

from domains.wiki.identity import Candidate, EntityRecord, normalize_name, slugify
from domains.wiki.source_summary import SourceClaim, SourceSummary
from domains.wiki.state import connection, insert_entity
from workflows.wiki_synthesis.entity_assignment import assign_summary, group_by_entity, match_claim

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


def _extract_returning(*candidate_names: str):
    """A fake extract_fn: whatever entities the summariser 'found' in the claims,
    as plain Candidates (no matched_id — resolution decides reuse vs mint)."""

    def _extract(item):
        return {
            "candidates": [Candidate(name=n, page_type="concept") for n in candidate_names],
            "llm_calls": [],
        }

    return _extract


def _summary(item_id: str, *claims: SourceClaim) -> SourceSummary:
    return SourceSummary(item_id=item_id, content_date="2026-03-01", claims=list(claims))


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

    result = assign_summary(summary, db_path=wiki_db_path, extract_fn=_extract_returning("OpenAI"))

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

    result = assign_summary(
        summary, db_path=wiki_db_path, extract_fn=_extract_returning("Anthropic")
    )

    assert len(result.new_entities) == 1
    minted = result.new_entities[0]
    assert minted.canonical_name == "Anthropic"
    assert result.assignments[0].entity_ids == (minted.entity_id,)


def test_residual_claim_is_mapped_by_injected_llm(wiki_db_path):
    # Claim 2 has an implicit subject ("It ...") — no surface form to match, so it
    # is a residual. The injected mapper receives ONLY the residual text (+ the
    # candidate names) and maps it to "Anthropic"; assign_summary resolves that
    # name back to the entity id and merges it into the claim's assignment.
    summary = _summary(
        "https://medium.com/p/imp",
        SourceClaim(text="Anthropic is an AI lab.", source_id="https://medium.com/p/imp"),
        SourceClaim(text="It raised $2B in fresh funding.", source_id="https://medium.com/p/imp"),
    )
    seen = {}

    def fake_residual(texts, names):
        seen["texts"], seen["names"] = list(texts), list(names)
        return [["Anthropic"]]

    result = assign_summary(
        summary,
        db_path=wiki_db_path,
        extract_fn=_extract_returning("Anthropic"),
        map_residual=fake_residual,
    )

    ent = result.new_entities[0].entity_id
    assert result.assignments[0].entity_ids == (ent,)  # deterministic
    assert result.assignments[1].entity_ids == (ent,)  # residual, LLM-mapped
    assert seen["texts"] == ["It raised $2B in fresh funding."]
    assert "Anthropic" in seen["names"]


def test_group_by_entity_flags_peripheral_comention(wiki_db_path):
    # "Anthropic" is the subject of 3 claims (central); "OpenAI" appears once, as
    # a passing co-mention in claim 3 (peripheral). group_by_entity inverts the
    # claim→entity mapping and flags salience via the shared deterministic gate
    # over the summary body, so Slice 3 can skip attributed pages for co-mentions.
    sid = "https://medium.com/p/grp"
    summary = _summary(
        sid,
        SourceClaim(text="Anthropic released Claude.", source_id=sid),
        SourceClaim(text="Anthropic raised fresh funding.", source_id=sid),
        SourceClaim(text="Anthropic, unlike OpenAI, focuses on safety.", source_id=sid),
    )

    result = assign_summary(
        summary, db_path=wiki_db_path, extract_fn=_extract_returning("Anthropic", "OpenAI")
    )
    groups = {g.entity.canonical_name: g for g in group_by_entity(result)}

    assert len(groups["Anthropic"].claims) == 3
    assert groups["Anthropic"].salient is True
    assert len(groups["OpenAI"].claims) == 1
    assert groups["OpenAI"].salient is False


def test_residual_mapper_reassembles_by_index(monkeypatch):
    # The production residual mapper turns the structured LLM response back into a
    # per-claim list in INPUT order. The model may return indices out of order and
    # omit a claim it couldn't resolve — that claim must come back as an empty list.
    from workflows.wiki_synthesis import entity_assignment as ea

    def fake_structured(prompt, *, schema, system="", model=""):
        result = schema(
            mappings=[
                {"claim_index": 2, "entity_names": ["OpenAI"]},
                {"claim_index": 0, "entity_names": ["Anthropic", "Claude"]},
            ]
        )
        return result, make_llm_call()

    monkeypatch.setattr(ea, "generate_structured_with_usage", fake_structured)

    mapped = ea.map_residual_llm(
        ["claim zero", "claim one", "claim two"], ["Anthropic", "Claude", "OpenAI"]
    )

    assert mapped == [["Anthropic", "Claude"], [], ["OpenAI"]]


def test_residual_mapper_short_circuits_on_no_claims(monkeypatch):
    # No residual claims → no LLM call at all (cost guard).
    from workflows.wiki_synthesis import entity_assignment as ea

    def boom(*a, **k):
        raise AssertionError("LLM must not be called for empty residual set")

    monkeypatch.setattr(ea, "generate_structured_with_usage", boom)
    assert ea.map_residual_llm([], ["Anthropic"]) == []


def test_assign_persists_nothing(wiki_db_path):
    # The "persists nothing" invariant is load-bearing: assign_summary MINTS an
    # entity id but must NOT write it — Slice 3 owns persistence. The DB is empty
    # before and after; the minted entity lives only in new_entities.
    from domains.wiki.state import connection, get_all_entities

    summary = _summary(
        "https://medium.com/p/np",
        SourceClaim(text="Cohere shipped a new model.", source_id="https://medium.com/p/np"),
    )
    result = assign_summary(summary, db_path=wiki_db_path, extract_fn=_extract_returning("Cohere"))

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

    result = assign_summary(
        summary, db_path=wiki_db_path, extract_fn=_extract_returning("OpenAI", "openai")
    )

    assert result.assignments[0].entity_ids == (existing_id,)
    assert result.new_entities == ()


def test_residual_name_not_in_candidates_is_dropped(wiki_db_path):
    # A residual mapper that hallucinates a name outside the resolved candidate
    # set contributes nothing — the claim stays unassigned rather than inventing
    # an entity link.
    sid = "https://medium.com/p/hallucinate"
    summary = _summary(
        sid,
        SourceClaim(text="Anthropic is an AI lab.", source_id=sid),
        SourceClaim(text="It later expanded.", source_id=sid),
    )

    def hallucinating_residual(texts, names):
        return [["Nonexistent Corp"]]

    result = assign_summary(
        summary,
        db_path=wiki_db_path,
        extract_fn=_extract_returning("Anthropic"),
        map_residual=hallucinating_residual,
    )

    assert result.assignments[1].entity_ids == ()
