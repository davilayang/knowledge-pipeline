"""Source-summary parser (Layer 1.5) — turn a source_writer summary body into
structured SourceClaim records.

The source_writer emits one markdown list item per claim, each prefixed with a
`[fact]` or `[speculation]` tag. The parser stamps the source's item_id onto
every claim and carries the tag as the `speculative` flag, producing the exact
SourceClaim shape the confidence-lane gate consumes downstream.
"""

from domains.wiki.source_summary import (
    SourceClaim,
    SourceSummary,
    parse_source_summary,
    parse_source_summary_doc,
    render_source_summary,
)


def test_parses_fact_and_speculation_tagged_lines_into_source_claims():
    body = (
        "- [fact] Claude Code shipped subagents in March 2026.\n"
        "- [speculation] Agentic orchestration will replace most RAG by 2027.\n"
    )

    claims = parse_source_summary(body, source_id="medium::https://x.com/a")

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
        "## Source summary\n"
        "\n"
        "This article covers Claude Code's agent features.\n"
        "- [fact] Claude Code added a subagent tool.\n"
        "- An untagged bullet that is not a claim.\n"
    )

    claims = parse_source_summary(body, source_id="medium::https://x.com/b")

    assert claims == [
        SourceClaim(
            text="Claude Code added a subagent tool.",
            source_id="medium::https://x.com/b",
            speculative=False,
        ),
    ]


def test_render_then_parse_doc_round_trips_the_source_summary():
    summary = SourceSummary(
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

    text = render_source_summary(summary)

    assert parse_source_summary_doc(text) == summary


def test_round_trips_a_source_with_no_content_date():
    summary = SourceSummary(
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

    text = render_source_summary(summary)

    assert parse_source_summary_doc(text) == summary
