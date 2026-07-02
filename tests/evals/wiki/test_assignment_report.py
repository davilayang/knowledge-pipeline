"""Assignment diagnostic — aggregate entity-assignment coverage over the
extract-claims corpus, so Slice 2's behaviour on the real corpus is visible
(how many claims get an entity,
which claims orphan) before attributed pages are built in Slice 3."""

from datetime import UTC, datetime

from domains.wiki.claims import ClaimSet, SourceClaim, render_claims
from domains.wiki.identity import EntityRecord
from evals.wiki.claims.assignment_report import (
    build_assignment_report,
    render_assignment_report,
)
from workflows.wiki_synthesis.entity_assignment import ClaimAssignment, SummaryAssignment


def _entity(entity_id: str, name: str) -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id,
        canonical_name=name,
        normalized_name=name.lower(),
        slug=name.lower(),
        page_type="concept",
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def test_report_counts_coverage():
    # One summary, two claims: claim 1 assigned to an entity, claim 2 orphaned
    # (no entity). Coverage is 1/2; one entity, one group.
    sid = "https://medium.com/p/a"
    claims = [
        SourceClaim(text="Anthropic released Claude.", source_id=sid),
        SourceClaim(text="It was widely discussed.", source_id=sid),
    ]
    summary = ClaimSet(item_id=sid, content_date="2026-03-01", claims=claims)
    ent = _entity("e_1", "Anthropic")
    assignment = SummaryAssignment(
        item_id=sid,
        assignments=(
            ClaimAssignment(claim=claims[0], entity_ids=("e_1",)),
            ClaimAssignment(claim=claims[1], entity_ids=()),
        ),
        entities={"e_1": ent},
        new_entities=(ent,),
    )

    doc = render_claims(summary)
    report = build_assignment_report([(sid, doc)], assign=lambda _s: assignment)

    assert report.n_summaries == 1
    assert report.n_claims == 2
    assert report.n_assigned == 1
    assert report.n_entities == 1
    assert report.n_groups == 1
    assert "coverage" in render_assignment_report(report).lower()
