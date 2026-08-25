"""claim_citations_hold_up — the asset check, run against a real queue.db.

Covers the path the unit tests cannot: the check reads the claim doc and the
fetched body back out of the store, so a claim's cited unit indices have to
survive being rendered into `extraction_calls.output` and parsed out again.
"""

from pathlib import Path

import dagster as dg
from domains.wiki.claims import ClaimSet, SourceClaim, render_claims
from orchestrators.defs.fetch_extract_queue.checks import check_claim_citations
from orchestrators.defs.shared.queue_resources import QueueStoreResource

from .test_assets import _seed_with_raw_content

_BODY = (
    "Anthropic shipped subagents in March 2026. "
    "We trained the model on 8 GPUs. "
    "Adoption has been slower than expected."
)


def _run_check(tmp_path: Path, claims: list[SourceClaim], body: str = _BODY):
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "YouTube", body)
    store = QueueStoreResource(db_path=str(db_path))
    store.record_claims(
        notion_page_id="p-1",
        output=render_claims(
            ClaimSet(item_id="https://example.com/x", content_date=None, claims=claims)
        ),
        prompt_label="extract_claims_v1",
        prompt_sha256="sha",
        model="gpt-4.1-mini",
        tokens_in=1,
        tokens_out=1,
    )
    return check_claim_citations("p-1", store)


def _claim(text: str, units: tuple[int, ...]) -> SourceClaim:
    return SourceClaim(text=text, source_id="https://example.com/x", cited_units=units)


def test_a_claim_whose_figure_is_in_its_cited_unit_passes(tmp_path: Path):
    result = _run_check(tmp_path, [_claim("Anthropic trained the model on 8 GPUs.", (1,))])

    assert result.passed
    assert result.metadata["grounded"].value == 1


def test_a_figure_the_source_never_contains_fails_the_check(tmp_path: Path):
    result = _run_check(tmp_path, [_claim("Anthropic trained the model on 64 GPUs.", (1,))])

    assert not result.passed
    assert result.metadata["unsupported"].value == 1
    assert result.severity == dg.AssetCheckSeverity.WARN


def test_a_claim_citing_a_unit_that_does_not_exist_fails(tmp_path: Path):
    result = _run_check(tmp_path, [_claim("Anthropic trained the model on 8 GPUs.", (99,))])

    assert not result.passed
    assert result.metadata["dangling"].value == 1


def test_the_check_reports_claims_citing_more_units_than_allowed(tmp_path: Path):
    result = _run_check(tmp_path, [_claim("Anthropic shipped subagents in 2026.", (0, 1, 2, 0))])

    assert result.metadata["over_cap"].value == 1


def test_a_row_with_no_claims_recorded_is_skipped_not_failed(tmp_path: Path):
    db_path = tmp_path / "q.db"
    _seed_with_raw_content(db_path, "p-1", "YouTube", _BODY)
    store = QueueStoreResource(db_path=str(db_path))

    result = check_claim_citations("p-1", store)

    assert result.passed
    assert result.metadata["skipped"].value is True
