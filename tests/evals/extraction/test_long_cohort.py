"""The long-content fixture cohort exists, loads, and every item is genuinely
long — the effect this workbench measures (tail loss on single-pass extraction)
only appears above ~30K chars, so short fixtures would silently no-op the test.
"""

from pathlib import Path

from evals.core import load_fixtures

REPO_ROOT = Path(__file__).resolve().parents[3]
LONG_COHORT = REPO_ROOT / "packages/evals/datasets/extraction_eval_long.jsonl"


def test_long_cohort_loads_and_every_item_exceeds_30k_chars():
    _header, rows = load_fixtures(LONG_COHORT, expected_schema_version=1)

    assert rows, "long cohort is empty"
    for row in rows:
        assert len(row["content"]) > 30_000, (
            f"{row['fixture_id']} is {len(row['content'])} chars — not long enough "
            "to exercise tail-coverage"
        )
