"""Tests for evals.extraction.variants — Variant factory for ThreeCallOpenAIExtractor."""

from unittest.mock import MagicMock

from evals.core import Variant, variant_identity
from evals.extraction.types import ExtractionFixture
from evals.extraction.variants import make_three_call_variant


# `make_three_call_variant` refuses a narrative prompt that does not name every
# field of `Narrative`, since a body written for an older shape would be sent
# with today's generated field list and scored as if it were that prompt's
# output. These tests care about variant plumbing, not prompt content, so the
# stub is the field names plus whatever marker the test is distinguishing by.
def _narrative_stub(marker: str = "") -> str:
    from domains.extraction.schemas import Narrative

    return marker + " " + " ".join(Narrative.model_fields)


def _fixture(content_type: str = "Article") -> ExtractionFixture:
    return ExtractionFixture(
        fixture_id="art_001",
        content_type=content_type,
        content="Lorem ipsum.",
        expected_topic_card={},
    )


def test_make_three_call_variant_returns_variant():
    v = make_three_call_variant(
        name="v5_baseline",
        narrative_prompt_text=_narrative_stub("N"),
        topic_card_prompt_text="T",
        followups_prompt_text="F",
        prompt_versions={"topic_card": "v1"},
        model="gpt-4o-mini",
        api_key="test-key",
    )
    assert isinstance(v, Variant)
    assert v.name == "v5_baseline"


def test_variant_provenance_carries_prompt_versions_and_model():
    v = make_three_call_variant(
        name="v5_baseline",
        narrative_prompt_text=_narrative_stub("N"),
        topic_card_prompt_text="T",
        followups_prompt_text="F",
        prompt_versions={"topic_card": "v1", "narrative": "v1", "followups": "v1"},
        model="gpt-4o-mini",
        api_key="test-key",
    )
    assert v.provenance.prompt_versions["topic_card"] == "v1"
    assert v.provenance.model_versions["extraction"] == "gpt-4o-mini"


def test_variant_identity_changes_when_prompt_text_changes():
    a = make_three_call_variant(
        name="a",
        narrative_prompt_text=_narrative_stub("N1"),
        topic_card_prompt_text="T1",
        followups_prompt_text="F",
        prompt_versions={"topic_card": "v1"},
        model="gpt-4o-mini",
        api_key="k",
    )
    b = make_three_call_variant(
        name="b",
        narrative_prompt_text=_narrative_stub("N1"),
        topic_card_prompt_text="T2",
        followups_prompt_text="F",
        prompt_versions={"topic_card": "v2"},
        model="gpt-4o-mini",
        api_key="k",
    )
    assert variant_identity(a) != variant_identity(b)


def test_variant_run_invokes_three_call_extractor(monkeypatch):
    fake_payload = MagicMock()
    fake_payload.topic_card.model_dump.return_value = {"extracted_title": "T"}
    fake_payload.narrative_md = "narrative"
    fake_payload.followups.model_dump.return_value = {"followups": []}
    fake_record = MagicMock(tokens_in=100, tokens_out=200, duration_ms=42.0)

    class StubExtractor:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def extract(self, content: str, *, content_type: str, content_shape: str):
            self.last_content_shape = content_shape
            return fake_payload, [fake_record, fake_record, fake_record]

    monkeypatch.setattr(
        "evals.extraction.variants.ThreeCallOpenAIExtractor",
        StubExtractor,
    )

    v = make_three_call_variant(
        name="v5",
        narrative_prompt_text=_narrative_stub("N"),
        topic_card_prompt_text="T",
        followups_prompt_text="F",
        prompt_versions={"topic_card": "v1"},
        model="gpt-4o-mini",
        api_key="k",
    )
    fixture = _fixture()
    run = v.run(fixture)
    assert run.status == "success"
    assert run.output["topic_card"] == {"extracted_title": "T"}
    assert run.tokens_in == 300
    assert run.tokens_out == 600


def test_variant_run_inside_running_event_loop(monkeypatch):
    """Jupyter regression: extract() uses asyncio.run() which crashes inside a
    running loop. The variant must detect the loop and thread-hop the call."""
    import asyncio

    fake_payload = MagicMock()
    fake_payload.topic_card.model_dump.return_value = {"extracted_title": "T"}
    fake_payload.narrative_md = "n"
    fake_payload.followups.model_dump.return_value = {"questions": []}
    fake_record = MagicMock(tokens_in=1, tokens_out=2, duration_ms=1.0)

    class StubExtractor:
        def __init__(self, **kwargs):
            pass

        def extract(self, content: str, *, content_type: str, content_shape: str):
            # Simulate the production extractor: would crash here if called
            # inside a running loop.
            asyncio.run(asyncio.sleep(0))
            return fake_payload, [fake_record, fake_record, fake_record]

    monkeypatch.setattr(
        "evals.extraction.variants.ThreeCallOpenAIExtractor",
        StubExtractor,
    )

    v = make_three_call_variant(
        name="v5",
        narrative_prompt_text=_narrative_stub("N"),
        topic_card_prompt_text="T",
        followups_prompt_text="F",
        prompt_versions={"topic_card": "v1"},
        model="gpt-4o-mini",
        api_key="k",
    )

    async def _drive():
        return v.run(_fixture())

    run = asyncio.run(_drive())
    assert run.status == "success"
    assert run.output["topic_card"] == {"extracted_title": "T"}


def test_variant_run_returns_error_status_on_extractor_failure(monkeypatch):
    class FailingExtractor:
        def __init__(self, **kwargs):
            pass

        def extract(self, content: str, *, content_type: str, content_shape: str):
            raise RuntimeError("upstream 500")

    monkeypatch.setattr(
        "evals.extraction.variants.ThreeCallOpenAIExtractor",
        FailingExtractor,
    )

    v = make_three_call_variant(
        name="v5",
        narrative_prompt_text=_narrative_stub("N"),
        topic_card_prompt_text="T",
        followups_prompt_text="F",
        prompt_versions={"topic_card": "v1"},
        model="gpt-4o-mini",
        api_key="k",
    )
    run = v.run(_fixture())
    assert run.status == "error"
    assert run.output is None
    assert "upstream 500" in run.error_message
