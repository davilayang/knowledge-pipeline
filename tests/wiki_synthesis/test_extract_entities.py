"""extract_entities — article + claims → candidate entities for the attributed lane.

The LLM call is mocked at the module boundary (extraction quality is validated
empirically). These tests pin the wiring: the `Name — type` output parses into
Candidates with normalised types, dedup, and no hard cap; the call uses the
shared-prefix layout (article cache-aligned with extract_claims) with the claims
in the task tail; and a NONE / malformed response is handled.
"""

from datetime import date
from unittest.mock import patch

from domains.types import IngestItem
from domains.wiki.claims import ClaimSet, SourceClaim
from workflows.llm import LLMCall
from workflows.wiki_synthesis.extract_entities import (
    extract_entities,
    parse_entity_candidates,
    render_candidates,
)


def _item(**overrides) -> IngestItem:
    fields = dict(
        item_id="medium::https://x.com/a",
        title="Why We Stopped Using Docker",
        date=date(2026, 3, 15),
        text="the article body naming Docker and Podman",
        source_type="raw_store",
        source_ref="raw_store:content_1",
        author=None,
    )
    fields.update(overrides)
    return IngestItem(**fields)


def _claims(*texts: str) -> ClaimSet:
    return ClaimSet(
        item_id="medium::https://x.com/a",
        content_date="2026-03-15",
        claims=[SourceClaim(text=t, source_id="medium::https://x.com/a") for t in texts],
    )


def _call(content: str) -> LLMCall:
    return LLMCall(content=content, model="gpt-4.1-mini", input_tokens=1, output_tokens=1)


# --- parse_entity_candidates (pure) ---------------------------------------------


def test_parses_name_and_type():
    cands = parse_entity_candidates("Docker — tool\nPodman — tool")
    assert [(c.name, c.page_type) for c in cands] == [("Docker", "tool"), ("Podman", "tool")]
    # The LLM never supplies ids/aliases — resolution owns identity.
    assert all(c.matched_id is None and c.aliases == [] for c in cands)


def test_normalises_type_synonyms_and_unknowns():
    text = (
        "Anthropic — org\n"
        "Cypher — technology\n"
        "CrossEncoder — tool/model\n"
        "ReAct paradigm — wibble\n"  # unknown → concept
    )
    cands = {c.name: c.page_type for c in parse_entity_candidates(text)}
    assert cands["Anthropic"] == "organization"
    assert cands["Cypher"] == "tool"
    assert cands["CrossEncoder"] == "tool"
    assert cands["ReAct paradigm"] == "concept"


def test_name_with_internal_hyphen_splits_on_last_separator():
    # "cross-encoder/ms-marco" has hyphens; only the final ' — type' is the type.
    (cand,) = parse_entity_candidates("cross-encoder/ms-marco-MiniLM-L-6-v2 — tool")
    assert cand.name == "cross-encoder/ms-marco-MiniLM-L-6-v2"
    assert cand.page_type == "tool"


def test_dedups_case_insensitively_first_spelling_wins():
    cands = parse_entity_candidates("Graphiti — tool\ngraphiti — tool\nGraphiti Core — tool")
    names = [c.name for c in cands]
    assert names == ["Graphiti", "Graphiti Core"]


def test_skips_bullets_blanks_none_and_prose_lines():
    text = (
        "- Docker — tool\n"  # bullet marker stripped
        "\n"  # blank
        "NONE\n"  # lone NONE skipped
        "This is a long sentence that is clearly prose and not an entity name at all here — other\n"
    )
    cands = parse_entity_candidates(text)
    assert [c.name for c in cands] == ["Docker"]


def test_no_hard_cap():
    text = "\n".join(f"Entity{i} — concept" for i in range(30))
    assert len(parse_entity_candidates(text)) == 30


def test_render_round_trips_through_parse():
    # render_candidates → parse_entity_candidates preserves name + page_type, so a
    # candidate set survives being stored and read back per source.
    from domains.wiki.identity import Candidate

    cands = [
        Candidate(name="Docker", page_type="tool"),
        Candidate(name="Michael Lanham", page_type="person"),
        Candidate(name="agentic RAG", page_type="concept"),
    ]
    reparsed = parse_entity_candidates(render_candidates(cands))
    assert [(c.name, c.page_type) for c in reparsed] == [(c.name, c.page_type) for c in cands]


def test_render_flattens_separator_inside_a_name():
    # A name containing the ` — ` separator must not corrupt the stored line — it
    # is flattened to a hyphen so the type delimiter stays unambiguous on read.
    from domains.wiki.identity import Candidate

    (reparsed,) = parse_entity_candidates(
        render_candidates([Candidate(name="ACME — Research", page_type="organization")])
    )
    assert reparsed.name == "ACME-Research"
    assert reparsed.page_type == "organization"


def test_strips_leading_list_numbering():
    # The model sometimes numbers despite "no numbering" — the digit must not
    # become part of the name.
    cands = parse_entity_candidates("1. Docker — tool\n2) Podman — tool")
    assert [c.name for c in cands] == ["Docker", "Podman"]


def test_drops_no_entity_phrasings():
    # "No entities — none" is the model saying there are none, not an entity.
    assert parse_entity_candidates("No entities — none") == []
    assert parse_entity_candidates("Docker — none") == []  # null type token


def test_trailing_punctuation_on_type_still_normalises():
    # "tool." must resolve to the tool PageType, not fall through to concept.
    (cand,) = parse_entity_candidates("Docker — tool.")
    assert cand.page_type == "tool"


def test_unspaced_separator_and_trailing_description():
    # Unspaced em dash is accepted; a trailing description after a second
    # separator keeps the real type (first-separator split).
    a = parse_entity_candidates("Docker—tool")
    assert (a[0].name, a[0].page_type) == ("Docker", "tool")
    b = parse_entity_candidates("Docker — tool — mentioned in passing")
    assert (b[0].name, b[0].page_type) == ("Docker", "tool")


# --- extract_entities (wiring, mocked LLM) --------------------------------------


def test_uses_shared_prefix_with_claims_in_tail():
    captured = {}

    def fake(messages, *, model, temperature):
        captured["messages"] = messages
        return _call("Docker — tool")

    with patch(
        "workflows.wiki_synthesis.extract_entities.generate_messages_with_usage", side_effect=fake
    ):
        cands, call = extract_entities(_item(), _claims("Docker was dropped for Podman."))

    messages = captured["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "user"]
    # Article body in the cacheable envelope; claims in the differing task tail.
    assert "the article body naming Docker and Podman" in messages[1]["content"]
    assert "Docker was dropped for Podman." in messages[2]["content"]
    assert "the article body naming Docker and Podman" not in messages[2]["content"]
    assert [c.name for c in cands] == ["Docker"]


def test_empty_claims_still_calls_with_placeholder():
    captured = {}

    def fake(messages, *, model, temperature):
        captured["messages"] = messages
        return _call("Docker — tool")

    with patch(
        "workflows.wiki_synthesis.extract_entities.generate_messages_with_usage", side_effect=fake
    ):
        extract_entities(_item(), _claims())

    assert "no claims extracted" in captured["messages"][-1]["content"]


def test_none_response_yields_no_candidates_without_warning(caplog):
    with patch(
        "workflows.wiki_synthesis.extract_entities.generate_messages_with_usage",
        return_value=_call("NONE"),
    ):
        cands, _ = extract_entities(_item(), _claims("x"))

    assert cands == []
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_malformed_response_logs_a_warning(caplog):
    with patch(
        "workflows.wiki_synthesis.extract_entities.generate_messages_with_usage",
        return_value=_call("I could not find any entities in this article, sorry."),
    ):
        cands, _ = extract_entities(_item(), _claims("x"))

    assert cands == []
    assert any(
        r.levelname == "WARNING" and "medium::https://x.com/a" in r.getMessage()
        for r in caplog.records
    )
