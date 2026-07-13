"""Tests for evals.extraction.wide — cite-by-index wide-schema variant.

The wide arm emits atomic Claims, each citing source unit indices (cited_indices)
into a pre-numbered source — not a verbatim quote. extract_fn is an injected seam
so tests drive the variant with a stub (no live LLM).
"""

from evals.core import Variant
from evals.extraction.types import ExtractionFixture
from evals.extraction.wide import make_wide_variant


def _fixture(content: str = "One. Two. Three.") -> ExtractionFixture:
    return ExtractionFixture(
        fixture_id="wide_001",
        content_type="Article",
        content=content,
        expected_topic_card={},
    )


def test_wide_variant_returns_success_run_with_cited_claims():
    def stub_extract(content: str) -> tuple[dict, int, int]:
        return (
            {
                "extracted_title": "T",
                "claims": [
                    {"text": "a claim", "cited_indices": [0, 1], "type": "claim"},
                ],
            },
            100,
            50,
        )

    v = make_wide_variant(
        name="wide", prompt_text="P", model="gpt-4.1-mini", extract_fn=stub_extract
    )
    assert isinstance(v, Variant)

    run = v.run(_fixture())
    assert run.status == "success"
    claim = run.output["claims"][0]
    assert claim["text"] == "a claim"
    assert claim["cited_indices"] == [0, 1]
    assert run.tokens_in == 100
    assert run.tokens_out == 50


def test_wide_variant_returns_error_run_when_extract_fn_raises():
    def failing_extract(content: str) -> tuple[dict, int, int]:
        raise RuntimeError("upstream 500")

    v = make_wide_variant(
        name="wide", prompt_text="P", model="gpt-4.1-mini", extract_fn=failing_extract
    )
    run = v.run(_fixture())
    assert run.status == "error"
    assert run.output is None
    assert "upstream 500" in run.error_message
