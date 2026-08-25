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


def test_a_line_is_a_unit_boundary_so_no_unit_spans_lines():
    # The numbered body tells the model "each line starts with its index".
    # Sentence punctuation alone breaks that: headings, list items and table
    # rows carry no terminal punctuation, so they glue onto their neighbours and
    # a citation lands on a whole section instead of a line.
    body = (
        "# Why Agents Matter\n\nAnthropic shipped subagents.\n\n"
        "- Runs in parallel\n- Shares no state\n"
    )

    units = build_citable_units(body)

    assert units == [
        "# Why Agents Matter",
        "Anthropic shipped subagents.",
        "- Runs in parallel",
        "- Shares no state",
    ]
    assert not any("\n" in u for u in units)


def test_a_sentence_wrapped_across_lines_stays_one_unit():
    # Caption tracks and prose both wrap mid-sentence. Treating every newline as
    # a boundary cuts those sentences in half: measured over the corpus, a plain
    # line split produced 1,104 half-sentences in transcripts alone.
    body = "Anthropic shipped subagents\nin March 2026. The rollout took two weeks.\n"

    assert build_citable_units(body) == [
        "Anthropic shipped subagents in March 2026.",
        "The rollout took two weeks.",
    ]
