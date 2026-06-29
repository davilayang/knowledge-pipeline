"""Per-source summary layer (Layer 1.5) — structured claims from one source.

The source_writer reads a raw article and emits a per-source summary: one
markdown list item per claim, each prefixed with a `[fact]` or `[speculation]`
tag. This module parses that body into `SourceClaim` records — the atomic unit
the confidence-lane gate clusters and routes downstream. Pure + dependency-free
(regex over the summary text): no LLM, no I/O.
"""

import re
from dataclasses import dataclass

# A claim line is a markdown list bullet, a tag in square brackets, then the
# claim text: `- [fact] Claude Code shipped subagents.`. Untagged lines
# (headings, prose, blanks) carry no claim and are skipped.
_CLAIM_LINE = re.compile(r"^\s*[-*]\s*\[(?P<tag>fact|speculation)\]\s*(?P<text>.+?)\s*$")


@dataclass(frozen=True)
class SourceClaim:
    """One atomic claim as asserted by ONE source summary. `source_id` is the
    item_id of the source the claim came from; `speculative` carries the source
    writer's `[speculation]` tag (prediction / opinion / unverified)."""

    text: str
    source_id: str
    speculative: bool = False


def parse_source_summary(body: str, *, source_id: str) -> list[SourceClaim]:
    """Parse a source_writer summary body into SourceClaim records.

    Each `[fact]`/`[speculation]`-tagged markdown list item becomes one claim:
    the tag sets `speculative`, the trailing text is the claim, and `source_id`
    is stamped on every claim. Lines without a recognised tag are ignored."""
    claims: list[SourceClaim] = []
    for line in body.splitlines():
        match = _CLAIM_LINE.match(line)
        if match is None:
            continue
        claims.append(
            SourceClaim(
                text=match["text"],
                source_id=source_id,
                speculative=match["tag"] == "speculation",
            )
        )
    return claims
