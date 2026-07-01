"""Per-source claim extractor (Layer 1.5).

Reads one raw article and produces a ClaimSet — the article's specific
claims, each tagged [reported]/[opinion] and attributed to the source. The
entity writer reads these summaries (never the raw article) so the wiki can
attribute a claim to a source rather than asserting it. The LLM call is a
faithful-capture step; its claim-extraction quality is validated empirically,
while the wiring below (item_id / date stamping, claim parsing) is unit-tested.
"""

import logging

from domains.types import IngestItem
from domains.wiki.claims import ClaimSet, parse_claims

from workflows.llm import LLMCall, generate_messages_with_usage
from workflows.wiki_synthesis.extract_shared import shared_prefix_messages
from workflows.wiki_synthesis.prompts import EXTRACT_CLAIMS_TASK

logger = logging.getLogger(__name__)

EXTRACT_CLAIMS_MODEL = "gpt-4.1-mini"

# Spoken content shapes (triage taxonomy). A long transcript's claims are mostly
# the speaker's opinions / forecasts; without this prior the model defaults most
# of them to [reported] at extraction scale, so the prompt's tag rule under-fires.
SPOKEN_SHAPES = frozenset({"conference_talk", "podcast_episode"})

_SHAPE_DESC = {
    "conference_talk": "a recorded conference talk — a speaker presenting to an audience",
    "podcast_episode": "a podcast episode — a conversational interview",
}


def _shape_prime(content_shape: str | None) -> str:
    """Leading prompt block that sets the [reported]/[opinion] prior for spoken
    sources; empty for text shapes (article / paper), which need no prior."""
    if content_shape not in SPOKEN_SHAPES:
        return ""
    return (
        f"This source is {_SHAPE_DESC[content_shape]}, and may be auto-transcribed. "
        "Most of what the speaker says is opinion, prediction, vision, or "
        "recommendation — tag those [opinion]. Reserve [reported] for concrete past "
        "events, releases, and measured numbers.\n\n"
    )


def extract_claims(
    item: IngestItem, *, content_shape: str | None = None
) -> tuple[ClaimSet, LLMCall]:
    """Extract claims from one source into a ClaimSet of tagged claims.

    `content_shape` (triage taxonomy) primes the [reported]/[opinion] tagging for
    spoken sources; None or a text shape leaves the prompt unprimed. A `NONE`
    response (no recordable claim) parses to zero claims — a valid outcome, not
    an error."""
    # Shared-prefix layout: the system + article envelope are byte-identical to the
    # downstream extract_entities call (built by the same shared_prefix_messages),
    # so the article prompt-caches across the two extract-time reads. Only this
    # claims task tail differs. The spoken-source [reported]/[opinion] prime rides
    # in the task tail (not the shared prefix) so it cannot vary the cached bytes.
    task = EXTRACT_CLAIMS_TASK.format(shape_prime=_shape_prime(content_shape))
    messages = shared_prefix_messages(item, task)
    # temperature=0: claim extraction is faithful capture, so pin the model to
    # its lowest-variance output for more reproducible summaries + evals. (The
    # API is not bit-deterministic even at 0 — claim counts still drift a little.)
    call = generate_messages_with_usage(messages, model=EXTRACT_CLAIMS_MODEL, temperature=0)
    claims = parse_claims(call.content, source_id=item.item_id)
    if not claims and "NONE" not in call.content:
        # Zero claims with no honest NONE — the model ignored the tagged-bullet
        # format. A silent empty summary would look identical to "no claims";
        # surface it so the failure is auditable rather than invisible.
        logger.warning(
            "claim_extractor parsed no claims for %s (no NONE marker); output starts: %r",
            item.item_id,
            call.content[:200],
        )
    summary = ClaimSet(
        item_id=item.item_id,
        content_date=item.date.isoformat() if item.date else None,
        claims=claims,
    )
    return summary, call
