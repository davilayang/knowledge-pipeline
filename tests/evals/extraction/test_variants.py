"""Tests for evals.extraction.variants — the Variant that scores /v1/extract.

The Jupyter thread-hop test that used to live here is gone with its subject: the
variant posted through an extractor whose `extract()` wrapped `asyncio.run`,
which crashed inside a notebook's running loop. An HTTP post has no loop of its
own to collide with.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from domains.extraction.schemas import Followups, Narrative, TopicCard
from evals.core import Variant, variant_identity
from evals.extraction.types import ExtractionFixture
from evals.extraction.variants import make_three_call_variant


@pytest.fixture
def prompts_dir(tmp_path: Path) -> Path:
    """A prompts tree with the labels these tests name.

    The narrative body lists every `Narrative` field because the variant refuses
    one that does not — a body written for an older shape would be sent with
    today's generated field list and scored as though it were that prompt's
    output. These tests are about plumbing, so the stub is the field names plus
    whatever marker distinguishes the arm.
    """
    directory = tmp_path / "prompts" / "extraction"
    directory.mkdir(parents=True)
    fields = " ".join(Narrative.model_fields)
    (directory / "narrative_v3.md").write_text(f"N {fields}")
    (directory / "narrative_v4.md").write_text(f"N4 {fields}")
    (directory / "topic_card_v1.md").write_text("T1")
    (directory / "topic_card_v2.md").write_text("T2")
    (directory / "followups_v1.md").write_text("F")
    return directory


def _variant(
    prompts_dir: Path, *, name="v3_baseline", narrative="narrative_v3", topic="topic_card_v1"
):
    return make_three_call_variant(
        name=name,
        prompt_versions={
            "narrative": narrative,
            "topic_card": topic,
            "followups": "followups_v1",
        },
        model="gpt-5-mini",
        service_url="http://fetcher:8000",
        prompts_dir=prompts_dir,
    )


def _fixture(content_type: str = "article") -> ExtractionFixture:
    return ExtractionFixture(
        fixture_id="art_001",
        content_type=content_type,
        content="Lorem ipsum.",
        expected_topic_card={},
    )


def _narrative_payload() -> dict:
    return Narrative(
        speakers_and_author="Alice Nkemdirim (Acme)",
        structure="one throughline - argues the core idea",
        core_idea="The core idea.",
        load_bearing_claims=["Claim one - anchor", "Claim two - anchor"],
        delivery_beats=["Beat one [Anchor: a figure] [From claims: 1]"],
        named_concepts_and_entities="Alice Nkemdirim, Acme",
    ).model_dump(mode="json")


def _ok_response(status_code: int = 200, **overrides) -> MagicMock:
    topic_card = TopicCard(
        extracted_title="T",
        core_mechanism="M does V to produce O.",
        best_example="ORG did SPECIFIC-THING.",
        transferable_pattern="Doing X lets you achieve Y.",
        main_tension="A vs B.",
    )
    body = {
        "results": [
            {"task": "narrative", "schema_version": 1, "payload": _narrative_payload()},
            {
                "task": "topic_card",
                "schema_version": 1,
                "payload": topic_card.model_dump(mode="json"),
            },
            {
                "task": "followups",
                "schema_version": 1,
                "payload": Followups(questions=["a?", "b?", "c?", "d?"]).model_dump(mode="json"),
            },
        ],
        "errors": [],
        "calls": [{"task": t, "tokens_in": 100, "tokens_out": 200} for t in ("n", "t", "f")],
        "cache_hits": [],
    }
    body.update(overrides)
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body
    return response


def test_make_three_call_variant_returns_variant(prompts_dir: Path):
    v = _variant(prompts_dir)
    assert isinstance(v, Variant)
    assert v.name == "v3_baseline"


def test_variant_provenance_carries_prompt_versions_and_model(prompts_dir: Path):
    v = _variant(prompts_dir)
    assert v.provenance.prompt_versions["topic_card"] == "topic_card_v1"
    assert v.provenance.model_versions["extraction"] == "gpt-5-mini"


def test_variant_identity_changes_when_a_prompt_changes(prompts_dir: Path):
    a = _variant(prompts_dir, name="a", topic="topic_card_v1")
    b = _variant(prompts_dir, name="b", topic="topic_card_v2")
    assert variant_identity(a) != variant_identity(b)


def test_variant_run_posts_the_named_prompt_versions(prompts_dir: Path):
    v = _variant(prompts_dir, narrative="narrative_v4")
    with patch("httpx.post", return_value=_ok_response()) as post:
        run = v.run(_fixture())

    assert post.call_args.args[0].endswith("/v1/extract")
    sent = post.call_args.kwargs["json"]
    assert sent["content"] == "Lorem ipsum."
    assert sent["model"] == "gpt-5-mini"
    assert sent["tasks"] == [
        {"task": "narrative", "prompt_version": "narrative_v4"},
        {"task": "topic_card", "prompt_version": "topic_card_v1"},
        {"task": "followups", "prompt_version": "followups_v1"},
    ]
    assert run.status == "success"
    assert run.output["topic_card"]["extracted_title"] == "T"
    # Rendered to the headed text the agent reads, not handed over as json.
    assert run.output["narrative_md"].startswith("Speakers and author:")
    assert run.tokens_in == 300
    assert run.tokens_out == 600


def test_variant_run_returns_error_status_when_the_service_fails(prompts_dir: Path):
    problem = MagicMock()
    problem.status_code = 503
    problem.json.return_value = {
        "title": "Extraction not configured",
        "detail": "no model set",
    }
    v = _variant(prompts_dir)
    with patch("httpx.post", return_value=problem):
        run = v.run(_fixture())

    assert run.status == "error"
    assert run.output is None
    assert "Extraction not configured" in run.error_message


def test_a_partial_batch_is_an_error_here_not_a_partial_score(prompts_dir: Path):
    """The pipeline tolerates a partial batch; a measurement cannot. A coverage
    number computed from two of three outputs is not comparable with one computed
    from three, and would be tabulated as though it were."""
    v = _variant(prompts_dir)
    partial = _ok_response(errors=[{"task": "followups", "detail": "no reply matched the schema"}])
    with patch("httpx.post", return_value=partial):
        run = v.run(_fixture())

    assert run.status == "error"
    assert "followups" in run.error_message


def test_a_narrative_prompt_written_for_another_schema_is_refused(prompts_dir: Path):
    """Refused at build time, before a run spends anything. Such a body still
    *runs* — the service appends today's generated field list regardless — so the
    result would be scored and tabulated as that prompt's, which is a wrong
    number wearing the shape of a measurement."""
    (prompts_dir / "narrative_ancient.md").write_text("Summarise the article.")
    with pytest.raises(ValueError, match="written for a different `Narrative` shape"):
        _variant(prompts_dir, narrative="narrative_ancient")


def test_the_variant_config_records_which_prompt_bodies_ran(prompts_dir: Path):
    """The label alone is not enough: labels get edited in place. The sha is what
    ties a recorded score to the bytes that produced it."""
    v = _variant(prompts_dir)
    assert len(v.config["narrative_prompt_sha"]) == 12
    assert v.config["extractor"] == "fetcher:/v1/extract"
    assert json.dumps(v.config)  # config has to survive the run record
