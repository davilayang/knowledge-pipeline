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


def summarize_source(item: IngestItem) -> tuple[SourceSummary, LLMCall]:
    """Summarise one article into a SourceSummary of tagged claims.

    A `NONE` response (the article carries no recordable claim) parses to zero
    claims — an empty source summary is a valid outcome, not an error."""
    author_line = f"Author: {item.author}\n" if item.author else ""
    user_prompt = SOURCE_SUMMARY_USER.format(
        title=item.title,
        author_line=author_line,
        article_text=item.text,
    )
    call = generate_with_usage(
        user_prompt, system=SOURCE_SUMMARY_SYSTEM, model=SOURCE_SUMMARY_MODEL
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
