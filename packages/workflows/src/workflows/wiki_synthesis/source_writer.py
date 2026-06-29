"""Per-source summary writer (Layer 1.5).

Reads one raw article and produces a SourceSummary — the article's specific
claims, each tagged [fact]/[speculation] and attributed to the source. The
entity writer reads these summaries (never the raw article) so the wiki can
attribute a claim to a source rather than asserting it. The LLM call is a
faithful-capture step; its claim-extraction quality is validated empirically,
while the wiring below (item_id / date stamping, claim parsing) is unit-tested.
"""

import logging

from domains.types import IngestItem
from domains.wiki.source_summary import SourceSummary, parse_source_summary

from workflows.llm import LLMCall, generate_with_usage
from workflows.wiki_synthesis.prompts import SOURCE_SUMMARY_SYSTEM, SOURCE_SUMMARY_USER

logger = logging.getLogger(__name__)

SOURCE_SUMMARY_MODEL = "gpt-4.1-mini"

# Spoken content shapes (triage taxonomy). A long transcript's claims are mostly
# the speaker's opinions / forecasts; without this prior the model defaults most
# of them to [fact] at extraction scale, so the prompt's tag rule under-fires.
SPOKEN_SHAPES = frozenset({"conference_talk", "podcast_episode"})

_SHAPE_DESC = {
    "conference_talk": "a recorded conference talk — a speaker presenting to an audience",
    "podcast_episode": "a podcast episode — a conversational interview",
}


def _shape_prime(content_shape: str | None) -> str:
    """Leading prompt block that sets the [fact]/[speculation] prior for spoken
    sources; empty for text shapes (article / paper), which need no prior."""
    if content_shape not in SPOKEN_SHAPES:
        return ""
    return (
        f"This source is {_SHAPE_DESC[content_shape]}, and may be auto-transcribed. "
        "Most of what the speaker says is opinion, prediction, vision, or "
        "recommendation — tag those [speculation]. Reserve [fact] for concrete past "
        "events, releases, and measured numbers.\n\n"
    )


def summarize_source(
    item: IngestItem, *, content_shape: str | None = None
) -> tuple[SourceSummary, LLMCall]:
    """Summarise one source into a SourceSummary of tagged claims.

    `content_shape` (triage taxonomy) primes the [fact]/[speculation] tagging for
    spoken sources; None or a text shape leaves the prompt unprimed. A `NONE`
    response (no recordable claim) parses to zero claims — a valid outcome, not
    an error."""
    author_line = f"Author: {item.author}\n" if item.author else ""
    user_prompt = SOURCE_SUMMARY_USER.format(
        shape_prime=_shape_prime(content_shape),
        title=item.title,
        author_line=author_line,
        article_text=item.text,
    )
    # temperature=0: claim extraction is faithful capture, so pin the model to
    # its lowest-variance output for more reproducible summaries + evals. (The
    # API is not bit-deterministic even at 0 — claim counts still drift a little.)
    call = generate_with_usage(
        user_prompt,
        system=SOURCE_SUMMARY_SYSTEM,
        model=SOURCE_SUMMARY_MODEL,
        temperature=0,
    )
    claims = parse_source_summary(call.content, source_id=item.item_id)
    if not claims and "NONE" not in call.content:
        # Zero claims with no honest NONE — the model ignored the tagged-bullet
        # format. A silent empty summary would look identical to "no claims";
        # surface it so the failure is auditable rather than invisible.
        logger.warning(
            "source_writer parsed no claims for %s (no NONE marker); output starts: %r",
            item.item_id,
            call.content[:200],
        )
    summary = SourceSummary(
        item_id=item.item_id,
        content_date=item.date.isoformat() if item.date else None,
        claims=claims,
    )
    return summary, call
