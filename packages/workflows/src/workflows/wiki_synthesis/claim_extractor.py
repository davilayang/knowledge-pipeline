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

from workflows.llm import LLMCall, generate_with_usage
from workflows.wiki_synthesis.prompts import EXTRACT_CLAIMS_SYSTEM, EXTRACT_CLAIMS_USER

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
    author_line = f"Author: {item.author}\n" if item.author else ""
    user_prompt = EXTRACT_CLAIMS_USER.format(
        shape_prime=_shape_prime(content_shape),
        title=item.title,
        author_line=author_line,
        article_text=item.text,
    )
    # Scope boundary: this call extracts + tags claims only — it deliberately does
    # NOT also assign an entity to each claim. Entity assignment is a separate
    # downstream step, kept isolated so the [reported]/[opinion] tagging (hard-won,
    # and noisy on unknown-shape news) can be tuned and measured without an entity
    # task confounding it. FOR LATER: a co-located variant (one call emitting tags
    # + per-claim entities) was tested and did NOT degrade tagging — N=3 on an
    # unknown-shape source gave opinion counts 5/0/0 (tags only) vs 3/4/2 (with
    # entities), i.e. within the model's own run-to-run noise — so folding entity
    # tagging in here is a viable consolidation if the extra downstream call ever
    # becomes a cost/latency concern.
    #
    # temperature=0: claim extraction is faithful capture, so pin the model to
    # its lowest-variance output for more reproducible summaries + evals. (The
    # API is not bit-deterministic even at 0 — claim counts still drift a little.)
    call = generate_with_usage(
        user_prompt,
        system=EXTRACT_CLAIMS_SYSTEM,
        model=EXTRACT_CLAIMS_MODEL,
        temperature=0,
    )
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
