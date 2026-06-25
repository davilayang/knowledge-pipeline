"""SpecificityJudge — does the page preserve the source's concrete specifics?

Built bottom-up: the deterministic anchor extractors first (numbers/dates), then
the LLM-backed name/quote anchors + abstraction flag, then the composed score.
"""

from evals.wiki.judges import (
    SpecificityJudge,
    anchor_recall,
    extract_date_anchors,
    extract_numeric_anchors,
    numbers_dates_recall,
)


def test_extract_numeric_anchors_keeps_money_percent_year_drops_bare_numbers():
    text = "Acme raised $5M in 2010, growing 30% YoY across 12 teams."

    anchors = extract_numeric_anchors(text)

    # high-signal specifics kept; bare "12" dropped as noise (codex: regex over-extracts)
    assert anchors == {"$5M", "2010", "30%"}


def test_anchor_recall_is_fraction_of_source_anchors_present_on_page():
    anchors = {"$5M", "2010", "30%"}
    page = "Acme raised $5M in 2010; momentum was strong."  # 30% dropped

    assert anchor_recall(anchors, page) == 2 / 3


def test_anchor_recall_no_source_anchors_is_vacuously_one():
    # nothing to preserve → no penalty (mirrors faithfulness's empty-claims = 1.0)
    assert anchor_recall(set(), "any page text") == 1.0


def test_extract_date_anchors_iso_and_month_year():
    text = "Released 2010-03-15, updated March 2011, and again in Jan 2012."

    anchors = extract_date_anchors(text)

    assert anchors == {"2010-03-15", "March 2011", "Jan 2012"}


def test_numbers_dates_recall_unions_numeric_and_date_anchors_across_sources():
    sources = ["Acme raised $5M in March 2011."]
    # source anchors: {$5M, 2011 (year), "March 2011" (date)} = 3
    page = "Acme raised $5M last decade."  # only $5M survives

    assert numbers_dates_recall(sources, page) == 1 / 3


def _stub_specifics(_prompt: str) -> dict:
    return {
        "names_orgs": [
            {"anchor": "Alice Smith", "preserved": True},
            {"anchor": "Globex", "preserved": False},
        ],
        "quotes": [{"quote": "we ship daily", "preserved": True}],
        "abstractions": [{"source_specific": "Alice Smith", "page_placeholder": "a researcher"}],
    }


def test_specificity_judge_composes_recalls_and_abstraction_penalty():
    judge = SpecificityJudge(chat_fn=_stub_specifics)

    score = judge.score(
        entity="Acme",
        page="Acme raised $5M.",
        sources=["Acme raised $5M; Alice Smith and Globex were involved."],
    )

    assert score.numbers_dates_recall == 1.0  # $5M survives
    assert score.names_orgs_recall == 1 / 2  # Alice kept, Globex dropped
    assert score.quote_recall == 1.0
    assert score.abstraction_penalty == 1
