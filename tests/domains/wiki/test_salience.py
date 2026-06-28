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
    entity_windows,
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


def test_entity_windows_single_mention_returns_only_the_passage():
    # A long article that names the entity once — windowing returns just the
    # passage around the mention, not the whole article.
    text = ("filler. " * 50) + "Globex makes turbines." + (" filler." * 50)

    windows = entity_windows(name="Globex", aliases=[], text=text, window_chars=20)

    assert len(windows) == 1
    assert "Globex makes turbines" in windows[0]
    assert windows[0] != text  # not the whole article
    assert len(windows[0]) < len(text)


def test_entity_windows_two_far_apart_mentions_return_two_windows():
    text = "Globex builds turbines. " + ("filler. " * 100) + "Globex also sells gears."

    windows = entity_windows(name="Globex", aliases=[], text=text, window_chars=20)

    assert len(windows) == 2
    assert "turbines" in windows[0]
    assert "gears" in windows[1]


def test_entity_windows_overlapping_mentions_merge_into_one():
    # Two mentions close together: their expanded windows overlap, so they merge
    # into a single passage rather than emitting the shared middle twice.
    text = ("x " * 50) + "Globex and then Globex again." + (" y" * 50)

    windows = entity_windows(name="Globex", aliases=[], text=text, window_chars=100)

    assert len(windows) == 1
    assert windows[0].count("Globex") == 2  # both mentions, shared span not duplicated


def test_entity_windows_absent_entity_returns_empty():
    text = "This article is entirely about other, unrelated things."

    assert entity_windows(name="Globex", aliases=[], text=text, window_chars=20) == []


def test_entity_windows_matches_aliases():
    # An entity named here by an alias ("MCP") is still windowed — same surface
    # pattern as the gate, so canonical + aliases all count.
    text = ("filler. " * 30) + "The MCP spec was published." + (" filler." * 30)

    windows = entity_windows(
        name="Model Context Protocol", aliases=["MCP"], text=text, window_chars=20
    )

    assert len(windows) == 1
    assert "MCP spec" in windows[0]


def test_entity_windows_clamps_to_text_start():
    # Mention at the very start — the window must not run before index 0.
    text = "Globex." + (" filler" * 100)

    windows = entity_windows(name="Globex", aliases=[], text=text, window_chars=50)

    assert len(windows) == 1
    assert windows[0].startswith("Globex")


def test_entity_windows_window_larger_than_text_returns_whole_text():
    text = "A short note on Globex here."

    windows = entity_windows(name="Globex", aliases=[], text=text, window_chars=10_000)

    assert windows == [text]


def test_entity_windows_adjacent_nonoverlapping_mentions_stay_separate():
    # Two mentions whose expanded windows do NOT touch → two windows (the merge
    # boundary complement of the overlapping case).
    text = "Globex" + (" " * 44) + "Globex"  # second mention at offset 50

    windows = entity_windows(name="Globex", aliases=[], text=text, window_chars=20)

    assert len(windows) == 2
