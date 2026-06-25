"""SpecificityJudge — does the page preserve the source's concrete specifics?

Built bottom-up: the deterministic anchor extractors first (numbers/dates), then
the LLM-backed name/quote anchors + abstraction flag, then the composed score.
"""

from evals.wiki.judges import extract_numeric_anchors


def test_extract_numeric_anchors_keeps_money_percent_year_drops_bare_numbers():
    text = "Acme raised $5M in 2010, growing 30% YoY across 12 teams."

    anchors = extract_numeric_anchors(text)

    # high-signal specifics kept; bare "12" dropped as noise (codex: regex over-extracts)
    assert anchors == {"$5M", "2010", "30%"}
