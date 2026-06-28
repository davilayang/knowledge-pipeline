"""Span-grounded synthesis: the synthesis prompt for an entity is built from the
entity's own passages (mention + context window), not the whole article — the
de-pollution lever. Driven through synthesize_from_candidates with the synthesis
LLM mocked to capture the prompt it actually receives.
"""

from pathlib import Path
from unittest.mock import patch

from domains.wiki.identity import Candidate
from workflows.wiki_synthesis.synthesize import synthesize_from_candidates

from tests.wiki_synthesis._helpers import build_synthesis_output, make_item, make_llm_call


def _capture_synthesis_prompt(item, candidates, *, db_path, wiki_dir) -> str:
    """Run synthesis with the LLM mocked; return the user prompt it was given."""
    captured: dict[str, str] = {}

    def capture(prompt, *, system="", model=""):
        captured["prompt"] = prompt
        return make_llm_call(content=build_synthesis_output(candidates[0].name))

    with patch("workflows.wiki_synthesis.synthesize.generate_with_usage", side_effect=capture):
        synthesize_from_candidates(item, candidates, db_path=db_path, wiki_dir=wiki_dir)
    return captured["prompt"]


def test_synthesis_prompt_excludes_offtopic_far_from_mentions(tmp_path: Path, wiki_db_path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    # "Globex" is salient (3 body mentions); an off-topic sentinel sits far
    # (> window_chars) from every mention, so windowing must drop it.
    intro = "ZEBRA_OFFTOPIC sentinel here. " + ("padding word. " * 60)
    cluster = "Globex builds turbines. Globex hired staff. Globex grew fast."
    text = intro + cluster + ("padding word. " * 60)
    item = make_item(
        item_id="content_span",
        title="Quarterly Report",
        text=text,
        source_ref="raw_store:content_span",
    )
    candidates = [Candidate(name="Globex", page_type="concept")]

    prompt = _capture_synthesis_prompt(item, candidates, db_path=wiki_db_path, wiki_dir=wiki_dir)

    assert "Globex" in prompt  # the entity's own passage is present
    assert "ZEBRA_OFFTOPIC" not in prompt  # off-topic content far from mentions dropped


def test_title_only_salient_entity_falls_back_to_full_body(tmp_path: Path, wiki_db_path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    # "Zenith" is salient via the TITLE but never named in the body → no windows
    # to narrow to, so synthesis falls back to the full body. Known limitation:
    # this class isn't de-polluted (the title still reaches the prompt separately).
    body = "This report covers many UNWINDOWED_BODY topics in detail. " * 5
    item = make_item(
        item_id="content_titleonly",
        title="Zenith annual review",
        text=body,
        source_ref="raw_store:content_titleonly",
    )
    candidates = [Candidate(name="Zenith", page_type="concept")]

    prompt = _capture_synthesis_prompt(item, candidates, db_path=wiki_db_path, wiki_dir=wiki_dir)

    assert "UNWINDOWED_BODY" in prompt  # full body fed (fallback), not an empty window
