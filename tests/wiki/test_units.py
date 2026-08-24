from domains.wiki.units import build_citable_units, number_units


def test_splits_body_into_one_unit_per_sentence():
    body = "Anthropic shipped subagents. The rollout took two weeks."
    assert build_citable_units(body) == [
        "Anthropic shipped subagents.",
        "The rollout took two weeks.",
    ]


def test_rewindows_a_sentence_too_long_to_localise_a_citation():
    # Auto-captioned transcripts can run thousands of chars without punctuation,
    # so sentence-splitting alone yields units too big to point at usefully.
    body = " ".join(["word"] * 400)  # ~2000 chars, no sentence break
    units = build_citable_units(body)
    assert len(units) > 1
    assert max(len(u) for u in units) <= 500
    assert " ".join(units) == body


def test_numbers_each_unit_with_the_index_a_claim_cites():
    assert number_units(["Anthropic shipped subagents.", "It took two weeks."]) == (
        "[0] Anthropic shipped subagents.\n[1] It took two weeks."
    )
