from domains.sessions.chunking import turn_grouping_chunker


def _serialize(turns: list[tuple[str, str, str]]) -> str:
    parts = []
    for role, ts, content in turns:
        parts.append(f"<<<TURN role={role} ts={ts}>>>")
        parts.append(content)
    return "\n".join(parts)


class TestTurnGroupingChunker:
    def test_empty_text_returns_no_chunks(self):
        assert turn_grouping_chunker("") == []

    def test_text_without_markers_returns_no_chunks(self):
        assert turn_grouping_chunker("plain text with no turns") == []

    def test_small_session_fits_in_one_chunk(self):
        text = _serialize(
            [
                ("user", "t1", "Hi"),
                ("assistant", "t2", "Hello"),
                ("user", "t3", "What is RAG?"),
                ("assistant", "t4", "Retrieval-augmented generation."),
            ]
        )
        chunks = turn_grouping_chunker(text, max_tokens=800)
        assert len(chunks) == 1
        assert chunks[0].text.count("<<<TURN") == 4
        assert chunks[0].index == 0

    def test_splits_when_max_tokens_exceeded(self):
        # Each turn ~250 chars (~62 tokens at 4 chars/token); max_tokens=80 →
        # ~320 chars, so two turns fit and the third forces a split.
        big = "a" * 250
        text = _serialize(
            [
                ("user", "t1", big),
                ("assistant", "t2", big),
                ("user", "t3", big),
                ("assistant", "t4", big),
            ]
        )
        chunks = turn_grouping_chunker(text, max_tokens=80, overlap_turns=0)
        assert len(chunks) >= 2
        # Sequential indexing.
        assert [c.index for c in chunks] == list(range(len(chunks)))

    def test_overlap_carries_turns_into_next_window(self):
        big = "a" * 250
        text = _serialize(
            [
                ("user", "t1", big),
                ("assistant", "t2", big),
                ("user", "t3", big),
                ("assistant", "t4", big),
            ]
        )
        chunks = turn_grouping_chunker(text, max_tokens=80, overlap_turns=1)
        assert len(chunks) >= 2
        # The last turn of chunk N appears as the first turn of chunk N+1.
        first_chunk_last_marker = chunks[0].text.rsplit("<<<TURN", 1)[-1].splitlines()[0]
        second_chunk_first_marker = chunks[1].text.split("\n", 1)[0]
        assert second_chunk_first_marker.endswith(first_chunk_last_marker.rstrip())

    def test_oversize_single_turn_emitted_alone(self):
        # One turn far exceeds max_tokens — must be emitted, not dropped.
        huge = "x" * 5000
        text = _serialize([("user", "t1", huge)])
        chunks = turn_grouping_chunker(text, max_tokens=80)
        assert len(chunks) == 1
        assert huge in chunks[0].text

    def test_heading_describes_time_range(self):
        text = _serialize(
            [
                ("user", "2026-04-01T14:00:00", "Hi"),
                ("assistant", "2026-04-01T14:01:00", "Hello"),
            ]
        )
        chunks = turn_grouping_chunker(text)
        assert chunks[0].heading == "turns 2026-04-01T14:00:00..2026-04-01T14:01:00"
