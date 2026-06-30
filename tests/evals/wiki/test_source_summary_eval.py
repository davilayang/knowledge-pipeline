"""Source-summary eval cohort loader + pinned-cohort integrity."""

from collections import Counter

from evals.wiki.source_summary.dataset import (
    DATASET_PATH,
    SourceFixture,
    load_source_fixtures,
)

SHAPES = {
    "tutorial",
    "opinion_essay",
    "conference_talk",
    "podcast_episode",
    "research_summary",
    "unknown",
}


def test_load_source_fixtures_returns_typed_nonempty_rows():
    fixtures = load_source_fixtures(DATASET_PATH)
    assert fixtures
    assert all(isinstance(f, SourceFixture) for f in fixtures)
    assert all(f.id and f.title and f.content_shape and f.body for f in fixtures)


def test_pinned_cohort_is_two_per_shape():
    counts = Counter(f.content_shape for f in load_source_fixtures(DATASET_PATH))
    assert set(counts) == SHAPES
    assert all(n == 2 for n in counts.values())
