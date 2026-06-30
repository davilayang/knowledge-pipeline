"""Confidence-lane admission gate for the layered wiki redesign (§3a).

A claim is never discarded — it is *routed* into a confidence lane that decides
whether the voice agent may assert it, attribute it, or only record it as open.
The gate is pure: claim matching takes an injected `embed_batch` callable and
specificity comes from an injected predicate, so this module has no LLM, HTTP, or
Dagster dependency and is testable with fakes.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from domains.wiki.source_summary import SourceClaim

EmbedBatch = Callable[[list[str]], list[list[float]]]


class Credibility(Enum):
    """How much a single source's bare assertion is worth. A high-credibility
    primary source (e.g. arxiv, an official blog) can admit a claim alone; two
    low-credibility echoes cannot."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# Primary / authoritative sources: a single one of these can carry a claim into a
# voice-safe section (the `single-credible` lane).
HIGH_CREDIBILITY_DOMAINS = frozenset({"arxiv.org"})

# Aggregator / open-publishing platforms: the corpus's bulk source (56% Medium).
# Two of these echoing one claim must not count as corroboration.
LOW_CREDIBILITY_DOMAINS = frozenset({"medium.com"})


def domain_credibility(domain: str) -> Credibility:
    """Map a source domain to its credibility tier. Unknown domains default to
    MEDIUM — neither trusted enough to admit alone nor dismissed as an echo."""
    if domain in HIGH_CREDIBILITY_DOMAINS:
        return Credibility.HIGH
    if domain in LOW_CREDIBILITY_DOMAINS:
        return Credibility.LOW
    return Credibility.MEDIUM


class Lane(Enum):
    """The treatment a claim receives, not just where it came from. The first two
    are voice-safe (the agent may assert them); the last two are recorded and
    visible but never voice-asserted."""

    CORPUS_CORROBORATED = "corpus_corroborated"  # ≥2 independent + specific
    SINGLE_CREDIBLE = "single_credible"  # 1 high-credibility primary
    SINGLE_SOURCE_ATTRIBUTED = "single_source_attributed"  # recorded, not asserted
    OPEN_SPECULATIVE = "open_speculative"  # speculative / contested / open


def route_lane(
    *,
    independent_source_count: int,
    max_credibility: Credibility,
    is_specific: bool,
    is_speculative: bool,
) -> Lane:
    """Route one matched claim into a confidence lane (§3a). A claim is never
    discarded — only routed to a lane that fixes how the voice agent may use it."""
    if is_speculative:
        return Lane.OPEN_SPECULATIVE
    # Specificity floor: a vague claim is recorded but never voice-asserted, even
    # when corroborated — this is what blocks abstraction laundering (§7 #2).
    if not is_specific:
        return Lane.SINGLE_SOURCE_ATTRIBUTED
    if independent_source_count >= 2:
        return Lane.CORPUS_CORROBORATED
    if max_credibility == Credibility.HIGH:
        return Lane.SINGLE_CREDIBLE
    return Lane.SINGLE_SOURCE_ATTRIBUTED


@dataclass(frozen=True)
class ClaimCluster:
    """A set of claims that assert the same thing (high embedding cosine), pooled
    across the sources that made them. `source_ids` is what "≥2 sources agree"
    counts — distinct sources, not distinct phrasings."""

    claims: tuple[SourceClaim, ...]

    @property
    def source_ids(self) -> frozenset[str]:
        return frozenset(c.source_id for c in self.claims)


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def cluster_claims(
    claims: list[SourceClaim],
    embed_batch: EmbedBatch,
    *,
    threshold: float = 0.80,
) -> list[ClaimCluster]:
    """Group claims whose embeddings are within `threshold` cosine into clusters
    (connected components of the >= threshold graph), so "the same claim" stated
    by N sources becomes one cluster carrying N source_ids. Pure over the injected
    `embed_batch`. Clusters are returned largest-first (ties by sorted source_ids)
    for deterministic downstream routing."""
    if not claims:
        return []
    vecs = embed_batch([c.text for c in claims])
    if len(vecs) != len(claims):
        raise ValueError(f"embedding count {len(vecs)} != claim count {len(claims)}")
    normed = [_normalize(v) for v in vecs]

    # Union-find over claim indices: union any pair with cosine >= threshold.
    parent = list(range(len(claims)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            cosine = sum(a * b for a, b in zip(normed[i], normed[j], strict=True))
            if cosine >= threshold:
                parent[find(i)] = find(j)

    groups: dict[int, list[SourceClaim]] = {}
    for idx, claim in enumerate(claims):
        groups.setdefault(find(idx), []).append(claim)

    clusters = [ClaimCluster(claims=tuple(members)) for members in groups.values()]
    clusters.sort(key=lambda c: (-len(c.source_ids), sorted(c.source_ids)))
    return clusters


@dataclass(frozen=True)
class RoutedClaim:
    """A claim cluster after the gate has assigned it a confidence lane."""

    cluster: ClaimCluster
    lane: Lane


# Credibility tiers ranked so a cluster takes its strongest source's tier.
_CREDIBILITY_RANK = {Credibility.LOW: 0, Credibility.MEDIUM: 1, Credibility.HIGH: 2}


def gate_claims(
    claims: list[SourceClaim],
    embed_batch: EmbedBatch,
    *,
    credibility_of: Callable[[str], Credibility],
    is_specific: Callable[[str], bool],
    threshold: float = 0.80,
) -> list[RoutedClaim]:
    """The admission gate (§3a): cluster claims by agreement, then route each
    cluster into a confidence lane. `credibility_of` maps a source_id to its
    domain credibility; `is_specific` judges each claim's text (the specificity
    floor). A cluster is specific only if EVERY paraphrase in it is — one vague
    member floors the whole cluster, so the lane can't depend on which paraphrase
    happened to arrive first. A cluster is speculative if any source tagged it so
    (both fail-closed). Independence is distinct source_ids in v1 — echo collapse
    over near-duplicate sources is the next refinement (§3a step 2)."""
    routed: list[RoutedClaim] = []
    for cluster in cluster_claims(claims, embed_batch, threshold=threshold):
        max_credibility = max(
            (credibility_of(sid) for sid in cluster.source_ids),
            key=lambda c: _CREDIBILITY_RANK[c],
        )
        lane = route_lane(
            independent_source_count=len(cluster.source_ids),
            max_credibility=max_credibility,
            is_specific=all(is_specific(c.text) for c in cluster.claims),
            is_speculative=any(c.speculative for c in cluster.claims),
        )
        routed.append(RoutedClaim(cluster=cluster, lane=lane))
    return routed
