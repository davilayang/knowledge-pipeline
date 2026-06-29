"""Per-source summary layer (Layer 1.5) — structured claims from one source.

The source_writer reads a raw article and emits a per-source summary: one
markdown list item per claim, each prefixed with a `[fact]` or `[speculation]`
tag. This module parses that body into `SourceClaim` records — the atomic unit
the confidence-lane gate clusters and routes downstream. Pure + dependency-free
(regex over the summary text): no LLM, no I/O.
"""

import re
from dataclasses import dataclass

import yaml

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


@dataclass(frozen=True)
class SourceSummary:
    """A whole per-source summary (Layer 1.5), as written to
    `wiki/sources/<slug>.md`. `item_id` is the source's `<source>::<url>` key;
    `content_date` is the source's publication date (ISO-8601, or None when the
    source carries none). `claims` are the tagged claims this source asserts."""

    item_id: str
    content_date: str | None
    claims: list[SourceClaim]


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


def render_source_summary(summary: SourceSummary) -> str:
    """Render a SourceSummary to the on-disk `wiki/sources/<slug>.md` format —
    YAML frontmatter (`item_id`, `content_date`) above a `[fact]`/`[speculation]`
    tagged bullet per claim. Inverse of `parse_source_summary_doc`."""
    frontmatter = yaml.dump(
        {"item_id": summary.item_id, "content_date": summary.content_date},
        default_flow_style=False,
        sort_keys=False,
    ).rstrip()
    bullets = "\n".join(
        f"- [{'speculation' if c.speculative else 'fact'}] {c.text}" for c in summary.claims
    )
    return f"---\n{frontmatter}\n---\n\n{bullets}\n"


def parse_source_summary_doc(text: str) -> SourceSummary:
    """Parse a rendered `wiki/sources/<slug>.md` doc into a SourceSummary —
    reads `item_id` / `content_date` from the frontmatter and the tagged claims
    from the body. Inverse of `render_source_summary`."""
    if not text.startswith("---"):
        raise ValueError("source summary does not start with frontmatter delimiter '---'")
    _, frontmatter, body = text.split("---", 2)
    meta = yaml.safe_load(frontmatter) or {}
    item_id = meta["item_id"]
    return SourceSummary(
        item_id=item_id,
        content_date=meta.get("content_date"),
        claims=parse_source_summary(body, source_id=item_id),
    )
