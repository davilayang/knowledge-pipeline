"""Deterministic source-salience gate — is the entity central enough to THIS
article to drive its page, or only mentioned in passing?

Pure text features (no LLM): mention count over canonical + aliases, title
presence, lead presence, first-mention position. The gate drops peripheral
edges so a one-mention article doesn't pollute an entity's page (measured: 53%
of page_source edges were low-salience).
"""

from domains.wiki.salience import (
    SalienceFeatures,
    count_mentions,
    is_salient,
    salience_features,
)


def test_count_mentions_is_case_insensitive_word_boundary_over_name_and_aliases():
    text = "Anthropic shipped Claude. anthropic also uses MCP; the protocol matters."

    # canonical "Anthropic" twice (case-insensitive) + alias "MCP" once = 3
    assert count_mentions("Anthropic", ["MCP"], text) == 3


def test_count_mentions_does_not_match_substrings():
    # "cat" must not hit "category"; word-boundary, not substring (relevance.py rule)
    text = "This category of catalysts is not about the animal."

    assert count_mentions("cat", [], text) == 0


def test_salience_features_for_a_central_entity():
    title = "Anthropic ships Claude 4"
    text = "Anthropic announced Claude 4 today. Anthropic said it is its best model."

    f = salience_features(name="Anthropic", aliases=[], title=title, text=text)

    assert f.mention_count == 2  # body only; title presence is the separate in_title
    assert f.in_title is True
    assert f.in_lead is True
    assert f.first_mention_ratio == 0.0  # first token of the body


def test_salience_features_for_a_peripheral_entity():
    title = "Anthropic ships Claude 4"
    # Globex named once, late, not in title, not in the lead
    text = "Anthropic announced Claude 4. " + ("filler text. " * 60) + "Globex also exists."

    f = salience_features(name="Globex", aliases=[], title=title, text=text)

    assert f.mention_count == 1
    assert f.in_title is False
    assert f.in_lead is False
    assert f.first_mention_ratio > 0.9  # appears near the very end


def _features(*, mention_count, in_title, in_lead=False, ratio=0.5):
    return SalienceFeatures(
        mention_count=mention_count,
        in_title=in_title,
        in_lead=in_lead,
        first_mention_ratio=ratio,
    )


def test_title_presence_attaches_even_with_one_mention():
    # codex baseline: in title → salient regardless of body frequency
    assert is_salient(_features(mention_count=1, in_title=True)) is True


def test_mention_floor_attaches_when_not_in_title():
    assert is_salient(_features(mention_count=3, in_title=False)) is True


def test_one_mention_not_in_title_is_peripheral():
    # the YOYO case: named once, not in title → dropped from page_sources
    assert is_salient(_features(mention_count=1, in_title=False)) is False
    assert is_salient(_features(mention_count=2, in_title=False)) is False
