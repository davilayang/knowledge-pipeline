from retrievers.chunking.registry import get_chunking_fn
from retrievers.chunking.turn_grouping import turn_grouping_chunker


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

    def test_oversize_turn_does_not_replicate_into_overlap(self):
        # Bug guard: an oversize turn that triggers emit must NOT be carried
        # forward as overlap, otherwise it appears in every adjacent chunk.
        huge = "x" * 5000
        normal = "a" * 100
        text = _serialize(
            [
                ("user", "t1", huge),
                ("assistant", "t2", normal),
                ("user", "t3", normal),
                ("assistant", "t4", normal),
            ]
        )
        chunks = turn_grouping_chunker(text, max_tokens=80, overlap_turns=2)
        # The oversize "x"*5000 turn should appear in exactly one chunk,
        # not duplicated through overlap into later chunks.
        huge_appearances = sum(1 for c in chunks if huge in c.text)
        assert (
            huge_appearances == 1
        ), f"oversize turn duplicated into {huge_appearances} chunks via overlap"

    def test_overlap_larger_than_window_terminates(self):
        # overlap_turns >= total turns should not loop infinitely.
        text = _serialize([("user", "t1", "Hi"), ("assistant", "t2", "Hello")])
        chunks = turn_grouping_chunker(text, max_tokens=800, overlap_turns=10)
        assert len(chunks) == 1

    def test_marker_collision_in_turn_body_splits_silently(self):
        # Documents the *known* failure mode: a turn body containing a line
        # that itself matches the marker pattern is treated as a turn boundary.
        # If this test starts failing because we added escaping, update it.
        colliding = "<<<TURN role=user ts=fake>>>\ninjected"
        text = _serialize([("user", "t1", f"a real turn\n{colliding}\ntrailing")])
        chunks = turn_grouping_chunker(text, max_tokens=800)
        # Two parsed "turns" instead of one — confirms the documented behavior.
        # Both end up in the same chunk because they fit under max_tokens.
        assert chunks[0].text.count("<<<TURN") == 2


class TestRegistryResolution:
    def test_registry_returns_turn_grouping(self):
        fn = get_chunking_fn("turn_grouping", chunk_size=200, chunk_overlap=10)
        text = _serialize([("user", "t1", "Hi"), ("assistant", "t2", "Hello there")])
        chunks = fn(text)
        assert len(chunks) == 1
        assert "<<<TURN" in chunks[0].text

    def test_registry_ignores_chunk_overlap(self):
        # The registry's chunk_overlap is in tokens; turn_grouping overlaps in
        # turns. Pin the documented no-op behavior so it surfaces in review if
        # the wiring changes.
        text = _serialize([("user", str(i), "a" * 200) for i in range(5)])
        # Two calls with very different chunk_overlap values must produce
        # identical output (overlap_turns is fixed at the module default).
        a = get_chunking_fn("turn_grouping", chunk_size=80, chunk_overlap=0)(text)
        b = get_chunking_fn("turn_grouping", chunk_size=80, chunk_overlap=999)(text)
        assert [c.text for c in a] == [c.text for c in b]
