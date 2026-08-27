"""Extract-claims eval cohort loader + pinned-cohort integrity."""

from collections import Counter

from evals.wiki.claims.dataset import (
    DATASET_PATH,
    SourceFixture,
    load_source_fixtures,
)

# Pinned rather than imported: the taxonomy lives in orchestrators, which sits above
# evals in the dependency order.
CONTENT_TYPES = {
    "article",
    "youtube",
    "arxiv",
    "medium",
    "facebook",
    "github",
    "file_pdf",
    "file_audio",
    "other",
}

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


def test_every_fixture_carries_a_real_content_type():
    """A blank or mis-cased content_type would load, run, and silently score an
    unprimed cohort."""
    fixtures = load_source_fixtures(DATASET_PATH)
    assert [f.id for f in fixtures if f.content_type not in CONTENT_TYPES] == []


def test_the_tutorial_pair_is_one_written_and_one_spoken():
    """With every genre label correct, no fixture's priming varied and the cohort was
    blind to gate changes. This pair splits written from spoken so over-tagging on
    instructional content reads as a gap between twins."""
    tutorials = [f for f in load_source_fixtures(DATASET_PATH) if f.content_shape == "tutorial"]
    assert sorted(f.content_type for f in tutorials) == ["medium", "youtube"]


def test_pinned_cohort_is_two_per_shape():
    counts = Counter(f.content_shape for f in load_source_fixtures(DATASET_PATH))
    assert set(counts) == SHAPES
    assert all(n == 2 for n in counts.values())
