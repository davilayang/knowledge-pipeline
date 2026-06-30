"""Confidence-lane admission gate (wiki layered redesign, §3a).

Built bottom-up: the deterministic credibility signal first, then the pure lane
routing, then claim matching over an injected embedder, then the composed gate.
"""

from domains.wiki.source_summary import SourceClaim
from evals.wiki.gate import (
    Credibility,
    Lane,
    cluster_claims,
    domain_credibility,
    gate_claims,
    route_lane,
)


def test_two_low_credibility_sources_do_not_corroborate():
    # Two Medium-tier (LOW) articles echoing one specific claim must NOT count as
    # corpus corroboration — all-low echoes stay single-source-attributed.
    lane = route_lane(
        independent_source_count=2,
        max_credibility=Credibility.LOW,
        is_specific=True,
        is_speculative=False,
    )
    assert lane == Lane.SINGLE_SOURCE_ATTRIBUTED


def test_two_sources_corroborate_when_one_is_at_least_medium():
    # ≥2 independent sources corroborate only when the cluster carries a non-LOW source.
    lane = route_lane(
        independent_source_count=2,
        max_credibility=Credibility.MEDIUM,
        is_specific=True,
        is_speculative=False,
    )
    assert lane == Lane.CORPUS_CORROBORATED


def test_domain_credibility_high_for_allowlisted_primary_source():
    # arxiv is on the high-credibility allowlist — a single such source can admit.
    assert domain_credibility("arxiv.org") == Credibility.HIGH


def test_domain_credibility_low_for_aggregator_blog():
    # Medium-style aggregators are the corpus's bulk source — two of them echoing
    # each other must not corroborate, so they sit in the LOW tier.
    assert domain_credibility("medium.com") == Credibility.LOW


def test_domain_credibility_unknown_domain_defaults_to_medium():
    assert domain_credibility("some-random-substack.example") == Credibility.MEDIUM


def test_route_lane_corpus_corroborated_when_two_independent_specific_sources():
    lane = route_lane(
        independent_source_count=2,
        max_credibility=Credibility.MEDIUM,
        is_specific=True,
        is_speculative=False,
    )
    assert lane == Lane.CORPUS_CORROBORATED


def test_route_lane_single_credible_when_one_high_credibility_specific_source():
    # The hollow-wiki rescue: a single arxiv/official source is voice-safe alone.
    lane = route_lane(
        independent_source_count=1,
        max_credibility=Credibility.HIGH,
        is_specific=True,
        is_speculative=False,
    )
    assert lane == Lane.SINGLE_CREDIBLE


def test_route_lane_single_source_attributed_when_one_medium_source():
    # One ordinary source: recorded as "one source claimed…", not voice-asserted.
    lane = route_lane(
        independent_source_count=1,
        max_credibility=Credibility.MEDIUM,
        is_specific=True,
        is_speculative=False,
    )
    assert lane == Lane.SINGLE_SOURCE_ATTRIBUTED


def test_route_lane_speculative_claim_is_open_even_if_corroborated():
    # A prediction/opinion never frames the entity, however many sources repeat it.
    lane = route_lane(
        independent_source_count=3,
        max_credibility=Credibility.HIGH,
        is_specific=True,
        is_speculative=True,
    )
    assert lane == Lane.OPEN_SPECULATIVE


def test_route_lane_specificity_floor_demotes_vague_corroborated_claim():
    # Abstraction laundering (§7 #2): "associated with geometric innovation" is
    # co-mentioned and faithful but vacuous — the specificity floor keeps it out
    # of voice-safe sections even though two sources carry it.
    lane = route_lane(
        independent_source_count=2,
        max_credibility=Credibility.HIGH,
        is_specific=False,
        is_speculative=False,
    )
    assert lane == Lane.SINGLE_SOURCE_ATTRIBUTED


def _fake_embed(vectors):
    """Return an embed_batch that looks each text up in `vectors` (text → vec)."""
    return lambda texts: [vectors[t] for t in texts]


def test_cluster_claims_groups_paraphrases_from_distinct_sources():
    # Two sources state the same claim in different words (high cosine) → one
    # cluster carrying both source_ids; an unrelated claim stays separate. This is
    # what makes "≥2 sources" mean agreement, not co-mention.
    claims = [
        SourceClaim(text="Anthropic builds Claude", source_id="s1"),
        SourceClaim(text="Claude is built by Anthropic", source_id="s2"),
        SourceClaim(text="Anthropic raised $2B", source_id="s3"),
    ]
    vectors = {
        "Anthropic builds Claude": [1.0, 0.0],
        "Claude is built by Anthropic": [0.99, 0.01],
        "Anthropic raised $2B": [0.0, 1.0],
    }

    clusters = cluster_claims(claims, embed_batch=_fake_embed(vectors), threshold=0.80)

    by_size = sorted(clusters, key=lambda c: -len(c.source_ids))
    assert by_size[0].source_ids == frozenset({"s1", "s2"})
    assert by_size[1].source_ids == frozenset({"s3"})


def test_gate_claims_routes_corroborated_cluster_to_voice_safe():
    # End-to-end: two sources agree on one specific factual claim → a single
    # routed claim in the corpus-corroborated (voice-safe) lane.
    claims = [
        SourceClaim(text="Anthropic builds Claude", source_id="s1"),
        SourceClaim(text="Claude is built by Anthropic", source_id="s2"),
    ]
    vectors = {
        "Anthropic builds Claude": [1.0, 0.0],
        "Claude is built by Anthropic": [0.99, 0.01],
    }

    routed = gate_claims(
        claims,
        embed_batch=_fake_embed(vectors),
        credibility_of=lambda _sid: Credibility.MEDIUM,
        is_specific=lambda _text: True,
    )

    assert len(routed) == 1
    assert routed[0].lane == Lane.CORPUS_CORROBORATED
    assert routed[0].cluster.source_ids == frozenset({"s1", "s2"})


def test_gate_claims_speculative_tag_on_any_source_opens_the_cluster():
    # One source states a prediction as reported, another tags it [opinion]; the
    # gate fails closed → the corroborated cluster lands in the open lane.
    claims = [
        SourceClaim(text="AGI arrives by 2027", source_id="s1", speculative=False),
        SourceClaim(text="AGI will arrive by 2027", source_id="s2", speculative=True),
    ]
    vectors = {
        "AGI arrives by 2027": [1.0, 0.0],
        "AGI will arrive by 2027": [0.99, 0.01],
    }

    routed = gate_claims(
        claims,
        embed_batch=_fake_embed(vectors),
        credibility_of=lambda _sid: Credibility.HIGH,
        is_specific=lambda _text: True,
    )

    assert routed[0].lane == Lane.OPEN_SPECULATIVE


def test_gate_claims_specificity_floor_is_order_independent_for_mixed_cluster():
    # A vague and a specific paraphrase cluster together (high cosine). The lane
    # must not depend on which arrived first — one vague member floors the whole
    # cluster to attributed-only, regardless of input order (codex).
    vague = SourceClaim(text="Anthropic is involved with AI", source_id="s1")
    specific = SourceClaim(text="Anthropic develops Claude", source_id="s2")
    vectors = {
        "Anthropic is involved with AI": [1.0, 0.0],
        "Anthropic develops Claude": [0.99, 0.01],
    }
    is_specific = lambda text: text == "Anthropic develops Claude"  # noqa: E731

    for order in ([vague, specific], [specific, vague]):
        routed = gate_claims(
            order,
            embed_batch=_fake_embed(vectors),
            credibility_of=lambda _sid: Credibility.MEDIUM,
            is_specific=is_specific,
        )
        assert routed[0].lane == Lane.SINGLE_SOURCE_ATTRIBUTED
