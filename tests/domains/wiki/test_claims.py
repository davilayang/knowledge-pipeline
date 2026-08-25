"""Claim-extraction parser (Layer 1.5) — turn a extract_claims claim body into
structured SourceClaim records.

The extract_claims emits one markdown list item per claim, each prefixed with a
`[reported]` or `[opinion]` tag. The parser stamps the source's item_id onto
every claim and carries the tag as the `speculative` flag, producing the exact
SourceClaim shape the confidence-lane gate consumes downstream.
"""

import re

import pytest
from domains.wiki.claims import (
    ClaimSet,
    SourceClaim,
    parse_claims,
    parse_claims_doc,
    render_claims,
    source_file_slug,
)


def test_parses_reported_and_opinion_tagged_lines_into_source_claims():
    body = (
        "- [reported] Claude Code shipped subagents in March 2026.\n"
        "- [opinion] Agentic orchestration will replace most RAG by 2027.\n"
    )

    claims = parse_claims(body, source_id="medium::https://x.com/a")

    assert claims == [
        SourceClaim(
            text="Claude Code shipped subagents in March 2026.",
            source_id="medium::https://x.com/a",
            speculative=False,
        ),
        SourceClaim(
            text="Agentic orchestration will replace most RAG by 2027.",
            source_id="medium::https://x.com/a",
            speculative=True,
        ),
    ]


def test_ignores_untagged_lines_headings_prose_and_blanks():
    body = (
        "## Claims\n"
        "\n"
        "This article covers Claude Code's agent features.\n"
        "- [reported] Claude Code added a subagent tool.\n"
        "- An untagged bullet that is not a claim.\n"
    )

    claims = parse_claims(body, source_id="medium::https://x.com/b")

    assert claims == [
        SourceClaim(
            text="Claude Code added a subagent tool.",
            source_id="medium::https://x.com/b",
            speculative=False,
        ),
    ]


def test_render_then_parse_doc_round_trips_the_extract_claims():
    summary = ClaimSet(
        item_id="medium::https://x.com/a",
        content_date="2026-03-15",
        claims=[
            SourceClaim(
                text="Claude Code shipped subagents in March 2026.",
                source_id="medium::https://x.com/a",
                speculative=False,
            ),
            SourceClaim(
                text="Agentic orchestration will replace most RAG by 2027.",
                source_id="medium::https://x.com/a",
                speculative=True,
            ),
        ],
    )

    text = render_claims(summary)

    assert parse_claims_doc(text) == summary


def test_render_rejects_a_claim_whose_source_id_is_not_the_summary_item_id():
    # render drops per-claim source_id and parse re-stamps from the frontmatter
    # item_id, so a foreign-source claim would be silently re-attributed. Fail loud.
    summary = ClaimSet(
        item_id="medium::https://x.com/a",
        content_date=None,
        claims=[
            SourceClaim(text="Mine.", source_id="medium::https://x.com/a"),
            SourceClaim(text="Not mine.", source_id="medium::https://x.com/OTHER"),
        ],
    )

    with pytest.raises(ValueError, match="source_id"):
        render_claims(summary)


def test_source_file_slug_is_deterministic_and_item_id_keyed():
    item_id = "medium::https://x.com/a"

    slug = source_file_slug(item_id)

    # Stable on the item_id (same source → same file, so writes overwrite
    # rather than orphan), and distinct from a different source.
    assert slug == source_file_slug(item_id)
    assert slug != source_file_slug("medium::https://x.com/b")
    assert slug.startswith("src_")
    assert re.fullmatch(r"src_[0-9a-f]{16}", slug)


def test_round_trips_an_item_id_containing_a_triple_dash():
    # A URL can contain '---'; a naive split('---') truncates the frontmatter and
    # corrupts the item_id (and drops content_date). The parse must be line-aware.
    summary = ClaimSet(
        item_id="medium::https://example.com/a---b/post",
        content_date="2026-03-15",
        claims=[
            SourceClaim(
                text="A claim from the triple-dash URL.",
                source_id="medium::https://example.com/a---b/post",
                speculative=False,
            ),
        ],
    )

    text = render_claims(summary)

    assert parse_claims_doc(text) == summary


def test_round_trips_a_source_with_no_content_date():
    summary = ClaimSet(
        item_id="medium::https://x.com/c",
        content_date=None,
        claims=[
            SourceClaim(
                text="The post is undated.",
                source_id="medium::https://x.com/c",
                speculative=False,
            ),
        ],
    )

    text = render_claims(summary)

    assert parse_claims_doc(text) == summary


def test_parses_the_cited_unit_indices_a_claim_carries():
    body = "- [reported|12,13] Claude Code shipped subagents in March 2026.\n"

    claims = parse_claims(body, source_id="medium::https://x.com/a")

    assert claims == [
        SourceClaim(
            text="Claude Code shipped subagents in March 2026.",
            source_id="medium::https://x.com/a",
            speculative=False,
            cited_units=(12, 13),
        )
    ]


def test_renders_cited_units_so_the_stored_doc_round_trips():
    summary = ClaimSet(
        item_id="medium::https://x.com/a",
        content_date="2026-03-15",
        claims=[
            SourceClaim(
                text="Claude Code shipped subagents in March 2026.",
                source_id="medium::https://x.com/a",
                cited_units=(12, 13),
            )
        ],
    )

    assert parse_claims_doc(render_claims(summary)) == summary


def test_a_space_separated_index_list_parses_instead_of_crashing():
    # The prompt asks for "comma-separated, no spaces"; a model that writes
    # "12 13" must not take down the extraction asset.
    claims = parse_claims("- [reported|12 13] X.\n", source_id="s")

    assert claims[0].cited_units == (12, 13)


def test_a_malformed_citation_suffix_keeps_the_claim():
    # Dropping the line loses the claim entirely, and the only alarm upstream
    # fires when EVERY claim is lost — so a handful of stray spaces would
    # silently shrink a source's claim set with nothing to show for it.
    for line in ("- [reported | 4] X.", "- [reported|4-6] X.", "- [reported|] X."):
        assert parse_claims(f"{line}\n", source_id="s"), line
