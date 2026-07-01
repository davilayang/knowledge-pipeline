"""Gate diagnostic — read source summaries, gate them, report lane distribution.
Pure adapters + aggregation TDD'd; the real corpus run is empirical."""

from evals.wiki.claims.gate_report import (
    build_gate_report,
    credibility_of,
    is_specific,
)
from evals.wiki.gate import Credibility, Lane


def test_credibility_of_maps_url_to_domain_tier():
    assert credibility_of("https://arxiv.org/abs/2401.0001") == Credibility.HIGH
    assert credibility_of("https://medium.com/@x/post") == Credibility.LOW
    assert credibility_of("https://www.medium.com/@x/post") == Credibility.LOW  # www stripped


def test_is_specific_true_only_with_a_concrete_anchor():
    assert is_specific("PAT achieves a 34% improvement on SPOT.") is True  # percent
    assert is_specific("Released in March 2026 to all users.") is True  # date
    assert is_specific("This will fundamentally change how teams work.") is False  # vague
    assert is_specific("Kubernetes replaced Docker as the default runtime.") is True  # 2 names
    # Sentence-lead capital must not count as a proper noun (the over-admission fix).
    assert is_specific("The API is very fast.") is False  # only "API" once The is excluded


def test_build_gate_report_aggregates_lanes_and_parse_failures():
    summaries = [
        ("p-1", _doc("u1", ["- [reported] PAT scores 34% on SPOT."])),
        ("p-2", "::: not a valid summary doc :::"),  # parse failure
    ]

    def fake_embed(texts):
        return [[1.0, 0.0] for _ in texts]  # all identical → one cluster

    report = build_gate_report(
        summaries, embed_batch=fake_embed, credibility_of=credibility_of, is_specific=is_specific
    )

    assert report.n_summaries == 2
    assert report.n_claims == 1
    assert len(report.parse_failures) == 1
    assert report.parse_failures[0][0] == "p-2"
    # One specific, non-speculative, single LOW (medium-less) source → attributed.
    assert report.lane_counts[Lane.SINGLE_SOURCE_ATTRIBUTED] == 1


def _doc(url: str, lines: list[str]) -> str:
    body = "\n".join(lines)
    return f"---\nitem_id: https://medium.com/{url}\ncontent_date: null\n---\n{body}\n"
