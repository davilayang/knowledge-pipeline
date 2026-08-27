"""Per-source claim extractor (Layer 1.5).

Reads one raw article and produces a ClaimSet — the article's specific
claims, each tagged [reported]/[opinion] and attributed to the source. The
entity writer reads these summaries (never the raw article) so the wiki can
attribute a claim to a source rather than asserting it. The LLM call is a
faithful-capture step; its claim-extraction quality is validated empirically,
while the wiring below (item_id / date stamping, claim parsing) is unit-tested.
"""

import logging
from dataclasses import replace

from domains.types import IngestItem
from domains.wiki.claims import ClaimSet, SourceClaim, parse_claims
from domains.wiki.units import build_citable_units

from workflows.llm import LLMCall, generate_messages_with_usage
from workflows.wiki_synthesis.extract_shared import shared_prefix_messages
from workflows.wiki_synthesis.prompts import EXTRACT_CLAIMS_TASK

logger = logging.getLogger(__name__)

EXTRACT_CLAIMS_MODEL = "gpt-4.1-mini"

# Content types whose body is a transcript of someone talking. A long transcript's
# claims are mostly the speaker's opinions / forecasts; without this prior the model
# defaults most of them to [reported] at extraction scale, so the prompt's tag rule
# under-fires.
#
# Gated on content_type rather than on a genre label because "is this a transcript"
# is a property of the medium, and content_type is decided by the fetcher's handler
# registry rather than inferred. The genre-label version of this gate primed only 66
# of 124 spoken production items: the other 58 were YouTube rows a URL-only
# classifier had called opinion_essay, tutorial, research_summary or nothing.
SPOKEN_CONTENT_TYPES = frozenset({"youtube", "file_audio"})

_SPOKEN_DESC = {
    "youtube": "a video transcript — a talk, interview or presentation",
    "file_audio": "an audio transcript — a podcast or recorded conversation",
}


def _spoken_prime(content_type: str | None) -> str:
    """Leading prompt block that sets the [reported]/[opinion] prior for transcripts;
    empty for written sources (article / paper), which need no prior."""
    if content_type not in SPOKEN_CONTENT_TYPES:
        return ""
    return (
        f"This source is {_SPOKEN_DESC[content_type]}, and may be auto-transcribed. "
        "Most of what the speaker says is opinion, prediction, vision, or "
        "recommendation — tag those [opinion]. Reserve [reported] for concrete past "
        "events, releases, and measured numbers.\n\n"
    )


def _drop_unresolvable_citations(claims: list[SourceClaim], *, n_units: int) -> list[SourceClaim]:
    """Strip cited indices that address no unit, keeping the claim.

    The model writes the indices as free text, so it can name a unit that does
    not exist. Such an index is indistinguishable from a real one once stored,
    and nothing downstream holds the body to tell them apart — so drop it at the
    only point where the unit count is still known. The claim itself is kept: its
    text may be perfectly faithful, and losing a statement is the worse error."""
    out: list[SourceClaim] = []
    for claim in claims:
        resolvable = tuple(i for i in claim.cited_units if 0 <= i < n_units)
        if len(resolvable) != len(claim.cited_units):
            logger.warning(
                "extract_claims dropped %d unresolvable citation(s) for %s (units=%d): %r",
                len(claim.cited_units) - len(resolvable),
                claim.source_id,
                n_units,
                claim.text[:120],
            )
        out.append(replace(claim, cited_units=resolvable))
    return out


def extract_claims(
    item: IngestItem, *, content_type: str | None = None
) -> tuple[ClaimSet, LLMCall]:
    """Extract claims from one source into a ClaimSet of tagged claims.

    `content_type` primes the [reported]/[opinion] tagging for transcript sources
    (see SPOKEN_CONTENT_TYPES); None or a written type leaves the prompt unprimed.
    A `NONE` response (no recordable claim) parses to zero claims — a valid
    outcome, not an error."""
    # Shared-prefix layout: the system + article envelope are byte-identical to the
    # downstream extract_entities call (built by the same shared_prefix_messages),
    # so the article prompt-caches across the two extract-time reads. Only this
    # claims task tail differs. The spoken-source [reported]/[opinion] prime rides
    # in the task tail (not the shared prefix) so it cannot vary the cached bytes.
    task = EXTRACT_CLAIMS_TASK.format(shape_prime=_spoken_prime(content_type))
    messages = shared_prefix_messages(item, task)
    # temperature=0: claim extraction is faithful capture, so pin the model to
    # its lowest-variance output for more reproducible summaries + evals. (The
    # API is not bit-deterministic even at 0 — claim counts still drift a little.)
    call = generate_messages_with_usage(messages, model=EXTRACT_CLAIMS_MODEL, temperature=0)
    claims = parse_claims(call.content, source_id=item.item_id)
    claims = _drop_unresolvable_citations(claims, n_units=len(build_citable_units(item.text)))
    if not claims and "NONE" not in call.content:
        # Zero claims with no honest NONE — the model ignored the tagged-bullet
        # format. A silent empty summary would look identical to "no claims";
        # surface it so the failure is auditable rather than invisible.
        logger.warning(
            "extract_claims parsed no claims for %s (no NONE marker); output starts: %r",
            item.item_id,
            call.content[:200],
        )
    summary = ClaimSet(
        item_id=item.item_id,
        content_date=item.date.isoformat() if item.date else None,
        claims=claims,
    )
    return summary, call
