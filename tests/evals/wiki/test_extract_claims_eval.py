"""Extract-claims eval cohort loader + pinned-cohort integrity."""

from collections import Counter

from evals.wiki.claims.dataset import (
    DATASET_PATH,
    SourceFixture,
    load_source_fixtures,
)

# Pinned literals rather than an import: the canonical taxonomy lives in
# `orchestrators.defs.triage_knowledge_queue.classify`, and `evals` sits below
# `orchestrators` in the dependency order, so it must not import from it.
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
    """content_type gates the transcript [opinion] prime. A blank or mis-cased value
    would load, run, and silently score an unprimed cohort — the same invisible skip
    the prime's gate was moved to fix."""
    fixtures = load_source_fixtures(DATASET_PATH)
    assert [f.id for f in fixtures if f.content_type not in CONTENT_TYPES] == []


def test_the_tutorial_pair_is_one_written_and_one_spoken():
    """The cohort was blind to a change in the prime's gate until this pair was split:
    with every fixture's genre label correct, no fixture's priming ever varied. Keeping
    one written and one spoken tutorial is what makes over-tagging on instructional
    content readable as a gap between twins."""
    tutorials = [f for f in load_source_fixtures(DATASET_PATH) if f.content_shape == "tutorial"]
    assert sorted(f.content_type for f in tutorials) == ["medium", "youtube"]


def test_pinned_cohort_is_two_per_shape():
    counts = Counter(f.content_shape for f in load_source_fixtures(DATASET_PATH))
    assert set(counts) == SHAPES
    assert all(n == 2 for n in counts.values())
