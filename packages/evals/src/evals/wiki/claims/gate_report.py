"""Gate diagnostic — run the confidence-lane gate over the whole extract-claims
corpus and report the lane distribution, so the gate's behaviour on the real
(~100%-Medium) corpus is visible before any attributed pages are built.

Adapters wire the pure gate (`evals.wiki.gate`) to production data: `credibility_of`
maps a claim's source_id (a canonical URL) to its domain tier; `is_specific` is a
deterministic specificity floor. The report parses every stored summary, gates the
claims, and aggregates — parse failures included, never swallowed.
"""

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse

from domains.wiki.claims import SourceClaim, parse_claims_doc

from evals.wiki.gate import Credibility, Lane, RoutedClaim, domain_credibility, gate_claims
from evals.wiki.judges import extract_date_anchors, extract_numeric_anchors


def credibility_of(source_id: str) -> Credibility:
    """Map a claim's source_id (a canonical URL) to its domain credibility tier."""
    domain = urlparse(source_id).netloc.removeprefix("www.")
    return domain_credibility(domain)


# A proper noun: a capitalised token of length ≥3. Two distinct ones = a named,
# checkable claim. Sentence-lead capitals ("The", "This", "Our") are excluded so a
# vague sentence isn't mistaken for a named one just because it starts capitalised.
_PROPER_NOUN = re.compile(r"\b[A-Z][A-Za-z0-9.\-]{2,}\b")
_SENTENCE_LEAD = frozenset(
    "The This That These Those Our Their Its They We You It If When While After "
    "Before Because However And But Or For With Without As At By In On To Of "
    "There Here Now Then Today".split()
)


def is_specific(text: str) -> bool:
    """Deterministic specificity floor (v0): a claim is specific if it carries a
    concrete anchor (a number or a date) or names ≥2 proper nouns (sentence-lead
    capitals excluded). Vague, anchor-free prose is floored to the attributed lane —
    this is what blocks abstraction laundering. Name/quote nuance is LLM-judged
    elsewhere; this is the cheap, pure production-side proxy the gate needs."""
    if extract_numeric_anchors(text) or extract_date_anchors(text):
        return True
    proper_nouns = {w for w in _PROPER_NOUN.findall(text) if w not in _SENTENCE_LEAD}
    return len(proper_nouns) >= 2


@dataclass(frozen=True)
class GateReport:
    n_summaries: int
    n_claims: int
    lane_counts: Counter[Lane]
    parse_failures: list[tuple[str, str]]
    routed: list[RoutedClaim] = field(default_factory=list)


def build_gate_report(
    summaries: list[tuple[str, str]],
    *,
    embed_batch: Callable[[list[str]], list[list[float]]],
    credibility_of: Callable[[str], Credibility],
    is_specific: Callable[[str], bool],
    threshold: float = 0.80,
) -> GateReport:
    """Parse `(page_id, rendered_doc)` summaries, gate the pooled claims, aggregate
    the lane distribution. A summary that won't parse is recorded as a failure (with
    its page_id + reason), not silently dropped."""
    claims: list[SourceClaim] = []
    parse_failures: list[tuple[str, str]] = []
    for page_id, output in summaries:
        try:
            summary = parse_claims_doc(output)
        except Exception as e:  # malformed/stale doc — surface it, don't swallow
            parse_failures.append((page_id, str(e)))
            continue
        claims.extend(summary.claims)
    routed = (
        gate_claims(
            claims,
            embed_batch,
            credibility_of=credibility_of,
            is_specific=is_specific,
            threshold=threshold,
        )
        if claims
        else []
    )
    return GateReport(
        n_summaries=len(summaries),
        n_claims=len(claims),
        lane_counts=Counter(r.lane for r in routed),
        parse_failures=parse_failures,
        routed=routed,
    )


def render_report(report: GateReport) -> str:
    """A skim-able markdown report of the gate's behaviour on the corpus."""
    lines = [
        "# Gate diagnostic",
        "",
        f"- summaries: {report.n_summaries}",
        f"- claims: {report.n_claims}",
        f"- clusters: {len(report.routed)}",
        f"- parse failures: {len(report.parse_failures)}",
        "",
        "## Lane distribution (clusters)",
        "",
    ]
    total = sum(report.lane_counts.values()) or 1
    for lane in Lane:
        n = report.lane_counts.get(lane, 0)
        lines.append(f"- `{lane.value}`: {n} ({n / total:.0%})")
    if report.parse_failures:
        lines += ["", "## Parse failures", ""]
        lines += [f"- `{pid}`: {reason}" for pid, reason in report.parse_failures]
    return "\n".join(lines) + "\n"
