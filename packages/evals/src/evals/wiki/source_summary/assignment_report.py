"""Assignment diagnostic — run Slice 2 entity assignment over the whole source-
summary corpus and report coverage, so its behaviour on the real corpus is
visible before attributed pages are built (Slice 3).

Reports what a human needs to eyeball precision without a labelled gold set yet:
claim coverage (share of claims that got an entity), the salient-vs-co-mention
split, and a sample of orphaned claims (assigned to no entity — the recall gap
the residual mapper is meant to close). Precision proper needs a gold pass; this
surfaces the inputs to judge it. `assign` is injected so the report is testable
without the extraction / residual LLM calls.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from domains.wiki.source_summary import SourceSummary, parse_source_summary_doc
from workflows.wiki_synthesis.entity_assignment import SummaryAssignment, group_by_entity

Assign = Callable[[SourceSummary], SummaryAssignment]


@dataclass(frozen=True)
class AssignmentReport:
    n_summaries: int
    n_claims: int
    n_assigned: int  # claims assigned to ≥1 entity
    n_entities: int  # distinct entities with ≥1 attributed claim (across the corpus)
    n_groups: int  # entity-in-source groups (an entity in two sources counts twice)
    n_salient_groups: int  # of those groups, central to their source (not a co-mention)
    orphans: list[tuple[str, str]] = field(default_factory=list)  # (source_id, claim_text)
    parse_failures: list[tuple[str, str]] = field(default_factory=list)


def build_assignment_report(
    summaries: list[tuple[str, str]],
    *,
    assign: Assign,
    max_orphans: int = 20,
) -> AssignmentReport:
    """Parse `(page_id, doc)` summaries, assign each, aggregate coverage. A doc
    that won't parse is recorded as a failure (page_id + reason), not dropped.
    Distinct entities are counted across the whole corpus by surrogate id, so an
    entity attributed claims in two summaries counts once."""
    n_claims = n_assigned = n_groups = n_salient_groups = 0
    entity_ids: set[str] = set()
    orphans: list[tuple[str, str]] = []
    parse_failures: list[tuple[str, str]] = []

    n_summaries = 0
    for page_id, doc in summaries:
        try:
            summary = parse_source_summary_doc(doc)
        except Exception as e:  # malformed/stale doc — surface it, don't swallow
            parse_failures.append((page_id, str(e)))
            continue
        n_summaries += 1
        result = assign(summary)
        for ca in result.assignments:
            n_claims += 1
            if ca.entity_ids:
                n_assigned += 1
            elif len(orphans) < max_orphans:
                orphans.append((ca.claim.source_id, ca.claim.text))
        # Salience is per SOURCE: the same entity can be central in one source and
        # a co-mention in another, so count it at the group (entity-in-source)
        # level, not collapsed by entity id (which would lose that split).
        for group in group_by_entity(result):
            entity_ids.add(group.entity.entity_id)
            n_groups += 1
            if group.salient:
                n_salient_groups += 1

    return AssignmentReport(
        n_summaries=n_summaries,
        n_claims=n_claims,
        n_assigned=n_assigned,
        n_entities=len(entity_ids),
        n_groups=n_groups,
        n_salient_groups=n_salient_groups,
        orphans=orphans,
        parse_failures=parse_failures,
    )


def render_assignment_report(report: AssignmentReport) -> str:
    """A skim-able markdown report of assignment behaviour on the corpus."""
    coverage = report.n_assigned / report.n_claims if report.n_claims else 0.0
    lines = [
        "# Entity-assignment diagnostic",
        "",
        f"- summaries: {report.n_summaries}",
        f"- claims: {report.n_claims}",
        f"- coverage (claims with an entity): "
        f"{report.n_assigned}/{report.n_claims} ({coverage:.0%})",
        f"- distinct entities (with ≥1 claim): {report.n_entities}",
        f"- entity-in-source groups: {report.n_groups} "
        f"({report.n_salient_groups} salient, "
        f"{report.n_groups - report.n_salient_groups} co-mention)",
        f"- parse failures: {len(report.parse_failures)}",
    ]
    if report.orphans:
        lines += ["", "## Orphaned claims (no entity assigned)", ""]
        lines += [f"- `{sid}`: {text}" for sid, text in report.orphans]
    if report.parse_failures:
        lines += ["", "## Parse failures", ""]
        lines += [f"- `{pid}`: {reason}" for pid, reason in report.parse_failures]
    return "\n".join(lines) + "\n"
