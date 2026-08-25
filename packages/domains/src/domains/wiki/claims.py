"""Per-source claim-extraction layer (Layer 1.5) — structured claims from one source.

The extract_claims reads a raw article and emits a per-source claim set: one
markdown list item per claim, each prefixed with a `[reported]` or `[opinion]`
tag. This module parses that body into `SourceClaim` records — the atomic unit
the confidence-lane gate clusters and routes downstream. Pure + dependency-free
(regex over the claim text): no LLM, no I/O.
"""

import hashlib
import re
from dataclasses import dataclass

import yaml

# A claim line is a markdown list bullet, a tag in square brackets, then the
# claim text: `- [reported|12,13] Claude Code shipped subagents.`. The `|...`
# suffix lists the source units the claim came from (see domains.wiki.units) and
# is OPTIONAL, so claim docs extracted before citations existed still parse.
# Untagged lines (headings, prose, blanks) carry no claim and are skipped.
#
# The suffix body is `[^\]]*` — anything up to the closing bracket — rather than
# a strict index list, so a claim survives a model that spaces or hyphenates its
# indices. A stricter pattern silently DROPS the whole claim on such a line, and
# the only upstream alarm fires when every claim is lost, so a handful of
# malformed suffixes would shrink a source's claim set invisibly.
_CLAIM_LINE = re.compile(
    r"^\s*[-*]\s*\[(?P<tag>reported|opinion)\s*(?:\|(?P<units>[^\]]*))?\]\s*(?P<text>.+?)\s*$"
)


@dataclass(frozen=True)
class SourceClaim:
    """One atomic claim as asserted by ONE source. `source_id` is the
    item_id of the source the claim came from; `speculative` carries the source
    writer's `[opinion]` tag (prediction / opinion / unverified); `cited_units`
    are the indices of the source units the claim came from (see
    domains.wiki.units), empty for a claim extracted before citations existed."""

    text: str
    source_id: str
    speculative: bool = False
    cited_units: tuple[int, ...] = ()


@dataclass(frozen=True)
class ClaimSet:
    """One source's whole extracted claim set (Layer 1.5), as written to
    `wiki/sources/<slug>.md`. `item_id` is the source's `<source>::<url>` key;
    `content_date` is the source's publication date (ISO-8601, or None when the
    source carries none). `claims` are the tagged claims this source asserts."""

    item_id: str
    content_date: str | None
    claims: list[SourceClaim]


def _parse_units(raw: str | None) -> tuple[int, ...]:
    """Pull the unit indices out of the `|12,13` suffix; empty when absent.

    Reads every run of digits rather than splitting on a delimiter. The prompt
    asks for "comma-separated, no spaces", and a model that writes `12 13` or
    `4-6` instead must not crash the extraction asset or lose the claim — the
    suffix is the model's, and this parser exists to absorb its variation."""
    return tuple(int(m) for m in re.findall(r"\d+", raw)) if raw else ()


def parse_claims(body: str, *, source_id: str) -> list[SourceClaim]:
    """Parse a extract_claims claim body into SourceClaim records.

    Each `[reported]`/`[opinion]`-tagged markdown list item becomes one claim:
    the tag sets `speculative`, the optional `|12,13` suffix sets `cited_units`,
    the trailing text is the claim, and `source_id` is stamped on every claim.
    Lines without a recognised tag are ignored."""
    claims: list[SourceClaim] = []
    for line in body.splitlines():
        match = _CLAIM_LINE.match(line)
        if match is None:
            continue
        claims.append(
            SourceClaim(
                text=match["text"],
                source_id=source_id,
                speculative=match["tag"] == "opinion",
                cited_units=_parse_units(match["units"]),
            )
        )
    return claims


def source_file_slug(item_id: str) -> str:
    """Deterministic `src_<16hex>` filename stem for a source's claim set, keyed
    by its item_id. Stable on the item_id so re-extracting a source's claims
    overwrites its file rather than orphaning it, and lets the entity writer
    locate a source's claim set by item_id alone — independent of the (cosmetic,
    mutable) title."""
    digest = hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:16]
    return f"src_{digest}"


def render_claims(summary: ClaimSet) -> str:
    """Render a ClaimSet to the on-disk `wiki/sources/<slug>.md` format —
    YAML frontmatter (`item_id`, `content_date`) above a `[reported]`/`[opinion]`
    tagged bullet per claim, each carrying its cited unit indices when it has
    any. Inverse of `parse_claims_doc`.

    Every claim must belong to this source: render drops per-claim `source_id`
    and the doc parser re-stamps it from the frontmatter `item_id`, so a claim
    from another source would be silently re-attributed. Reject it instead."""
    foreign = {c.source_id for c in summary.claims if c.source_id != summary.item_id}
    if foreign:
        raise ValueError(
            f"claim set for {summary.item_id} carries claims from other "
            f"source_id(s): {sorted(foreign)}"
        )
    frontmatter = yaml.dump(
        {"item_id": summary.item_id, "content_date": summary.content_date},
        default_flow_style=False,
        sort_keys=False,
    ).rstrip()
    bullets = "\n".join(
        f"- [{'opinion' if c.speculative else 'reported'}"
        f"{'|' + ','.join(str(u) for u in c.cited_units) if c.cited_units else ''}] {c.text}"
        for c in summary.claims
    )
    return f"---\n{frontmatter}\n---\n\n{bullets}\n"


def parse_claims_doc(text: str) -> ClaimSet:
    """Parse a rendered `wiki/sources/<slug>.md` doc into a ClaimSet —
    reads `item_id` / `content_date` from the frontmatter and the tagged claims
    from the body. Inverse of `render_claims`."""
    lines = text.splitlines(keepends=True)
    if not lines or not lines[0].startswith("---"):
        raise ValueError("claims doc does not start with frontmatter delimiter '---'")
    # Line-aware split on the closing '---' delimiter — a substring split would
    # break on a frontmatter value (e.g. an item_id URL) that contains '---'.
    close = next((i for i in range(1, len(lines)) if lines[i].startswith("---")), None)
    if close is None:
        raise ValueError("claims doc has no closing frontmatter delimiter '---'")
    frontmatter = "".join(lines[1:close])
    body = "".join(lines[close + 1 :])
    meta = yaml.safe_load(frontmatter) or {}
    item_id = meta["item_id"]
    return ClaimSet(
        item_id=item_id,
        content_date=meta.get("content_date"),
        claims=parse_claims(body, source_id=item_id),
    )
