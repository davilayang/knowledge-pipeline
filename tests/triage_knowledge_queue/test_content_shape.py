"""Tests for the rules-only content_shape classifier.

`classify_content_shape(enrichment, content_type, url)` returns one of:
`conference_talk` / `podcast_episode` / `tutorial` / `opinion_essay` /
`research_summary` / `unknown`.

Priority (first match wins):
1. arXiv host on `content_type` → `research_summary`.
2. Audio suffix on URL → `podcast_episode`.
3. YouTube channel match against per-shape lists.
4. Article host rules.
5. Fallback → `unknown`.
"""

from orchestrators.defs.triage_knowledge_queue.content_shape import (
    classify_content_shape,
)
from orchestrators.defs.triage_knowledge_queue.enrich import (
    ArxivSignals,
    EnrichmentSignals,
    YoutubeSignals,
)

# ---------------- arXiv ----------------


def test_arxiv_content_type_classifies_research_summary():
    """arXiv host trumps everything else — no need to inspect enrichment."""
    shape = classify_content_shape(
        enrichment=EnrichmentSignals(arxiv=ArxivSignals(title="t", categories=("cs.LG",))),
        content_type="arXiv",
        url="https://arxiv.org/abs/2105.04663",
    )
    assert shape == "research_summary"


# ---------------- audio URL ----------------


def test_audio_url_classifies_podcast_episode():
    shape = classify_content_shape(
        enrichment=EnrichmentSignals(),
        content_type="Podcast",
        url="https://podtrac.example.com/show.mp3",
    )
    assert shape == "podcast_episode"


def test_audio_url_classifies_podcast_even_when_content_type_article():
    """Defensive: if the URL ends in audio suffix but content_type wasn't
    classified as Podcast (e.g. user override), still classify the shape
    as podcast_episode based on URL alone."""
    shape = classify_content_shape(
        enrichment=EnrichmentSignals(),
        content_type="Article",
        url="https://example.com/episode.m4a",
    )
    assert shape == "podcast_episode"


# ---------------- youtube channel rules ----------------


def test_conference_channel_classifies_conference_talk():
    shape = classify_content_shape(
        enrichment=EnrichmentSignals(youtube=YoutubeSignals(channel="AI Engineer")),
        content_type="YouTube",
        url="https://www.youtube.com/watch?v=abc",
    )
    assert shape == "conference_talk"


def test_tutorial_channel_classifies_tutorial():
    shape = classify_content_shape(
        enrichment=EnrichmentSignals(youtube=YoutubeSignals(channel="AWS Developers")),
        content_type="YouTube",
        url="https://www.youtube.com/watch?v=abc",
    )
    assert shape == "tutorial"


def test_podcast_channel_classifies_podcast_episode():
    shape = classify_content_shape(
        enrichment=EnrichmentSignals(youtube=YoutubeSignals(channel="Lenny's Podcast")),
        content_type="YouTube",
        url="https://www.youtube.com/watch?v=abc",
    )
    assert shape == "podcast_episode"


def test_opinion_channel_classifies_opinion_essay():
    shape = classify_content_shape(
        enrichment=EnrichmentSignals(youtube=YoutubeSignals(channel="Every")),
        content_type="YouTube",
        url="https://www.youtube.com/watch?v=abc",
    )
    assert shape == "opinion_essay"


def test_unknown_youtube_channel_falls_through_to_unknown():
    shape = classify_content_shape(
        enrichment=EnrichmentSignals(youtube=YoutubeSignals(channel="Random Channel")),
        content_type="YouTube",
        url="https://www.youtube.com/watch?v=abc",
    )
    assert shape == "unknown"


def test_youtube_with_no_channel_falls_through_to_unknown():
    """oEmbed call failed → no channel signal → can't classify."""
    shape = classify_content_shape(
        enrichment=EnrichmentSignals(youtube=YoutubeSignals()),
        content_type="YouTube",
        url="https://www.youtube.com/watch?v=abc",
    )
    assert shape == "unknown"


# ---------------- article host rules ----------------


def test_substack_host_classifies_opinion_essay():
    shape = classify_content_shape(
        enrichment=EnrichmentSignals(),
        content_type="Article",
        url="https://ontologist.substack.com/p/my-essay",
    )
    assert shape == "opinion_essay"


def test_research_blog_host_classifies_research_summary():
    shape = classify_content_shape(
        enrichment=EnrichmentSignals(),
        content_type="Article",
        url="https://research.google/blog/something/",
    )
    assert shape == "research_summary"


def test_tutorial_host_classifies_tutorial():
    shape = classify_content_shape(
        enrichment=EnrichmentSignals(),
        content_type="Article",
        url="https://addyosmani.com/blog/tip-of-the-day",
    )
    assert shape == "tutorial"


def test_article_host_match_strips_www_prefix():
    """Host lookup should be case-insensitive and ignore the www. prefix —
    article URLs come in both forms."""
    shape = classify_content_shape(
        enrichment=EnrichmentSignals(),
        content_type="Article",
        url="https://www.kdnuggets.com/post",
    )
    assert shape == "opinion_essay"


def test_unknown_article_host_falls_through_to_unknown():
    shape = classify_content_shape(
        enrichment=EnrichmentSignals(),
        content_type="Article",
        url="https://random.example.com/post",
    )
    assert shape == "unknown"


# ---------------- empty enrichment / defensive ----------------


def test_empty_enrichment_for_youtube_falls_through_to_unknown():
    shape = classify_content_shape(
        enrichment=EnrichmentSignals(),
        content_type="YouTube",
        url="https://www.youtube.com/watch?v=abc",
    )
    assert shape == "unknown"


def test_other_content_type_returns_unknown():
    shape = classify_content_shape(
        enrichment=EnrichmentSignals(),
        content_type="Other",
        url="https://news.ycombinator.com/item?id=1",
    )
    assert shape == "unknown"


# ---------------- priority ordering ----------------


def test_arxiv_host_beats_youtube_channel_match():
    """Defensive: arXiv content_type trumps everything, even if an enrichment
    leak somehow puts a conference channel name in the signal."""
    shape = classify_content_shape(
        enrichment=EnrichmentSignals(youtube=YoutubeSignals(channel="AI Engineer")),
        content_type="arXiv",
        url="https://arxiv.org/abs/2105.04663",
    )
    assert shape == "research_summary"


def test_audio_url_beats_article_host_rule():
    """Defensive: if a substack somehow serves an .mp3 directly, podcast wins."""
    shape = classify_content_shape(
        enrichment=EnrichmentSignals(),
        content_type="Article",
        url="https://ontologist.substack.com/audio.mp3",
    )
    assert shape == "podcast_episode"


# ---------------- punctuation folding ----------------


def test_curly_apostrophe_in_channel_matches_ascii_yaml_entry():
    """YouTube oEmbed sometimes returns U+2019 (right single quote) where the
    YAML uses ASCII U+0027 — channel must still match its shape."""
    shape = classify_content_shape(
        enrichment=EnrichmentSignals(youtube=YoutubeSignals(channel="Lenny’s Podcast")),
        content_type="YouTube",
        url="https://www.youtube.com/watch?v=abc",
    )
    assert shape == "podcast_episode"


def test_curly_double_quote_in_channel_folds_to_ascii():
    """Defensive: if oEmbed ever wraps a channel name in U+201C / U+201D,
    the fold collapses them to the ASCII double quote. (No matching channel
    in seed YAML, but the fold path must not crash and must still let the
    classifier reach the fall-through.)"""
    shape = classify_content_shape(
        enrichment=EnrichmentSignals(youtube=YoutubeSignals(channel="“Quoted Channel”")),
        content_type="YouTube",
        url="https://www.youtube.com/watch?v=abc",
    )
    assert shape == "unknown"
