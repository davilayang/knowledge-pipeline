"""ASR segments mapped onto the `{text, start, duration}` caption-chunk shape,
so an audio-transcribed video produces the same artifacts as a captioned one.
"""

import pytest


def test_segments_map_onto_the_caption_chunk_shape() -> None:
    from fetcher.extractors.whisper import segments_to_chunks

    segments = [
        {"start": 0.36, "end": 2.92, "text": "first line"},
        {"start": 2.92, "end": 5.5, "text": "second line"},
    ]

    assert segments_to_chunks(segments, offset=0.0) == [
        {"text": "first line", "start": 0.36, "duration": 2.56},
        {"text": "second line", "start": 2.92, "duration": 2.58},
    ]


def test_chunk_offsets_come_from_measured_durations_not_the_nominal_segment_length() -> None:
    """ffmpeg cuts on keyframes and the last chunk is short, so chunk N does not
    start at N * 600s. The nominal offset would put chunk 2 at 1200.0, not 1199.6."""
    from fetcher.extractors.whisper import stitch_chunk_segments

    per_chunk = [
        ([{"start": 0.0, "end": 10.0, "text": "a"}], 598.4),  # measured, not 600
        ([{"start": 0.0, "end": 10.0, "text": "b"}], 601.2),
        ([{"start": 0.0, "end": 10.0, "text": "c"}], 42.0),
    ]

    chunks = stitch_chunk_segments(per_chunk)

    assert [c["start"] for c in chunks] == [0.0, 598.4, 1199.6]


def test_malformed_segments_are_dropped_and_output_stays_monotonic() -> None:
    """ASR output is model-generated, not schema-guaranteed. Malformed segments
    would corrupt the stitched timeline, so they are dropped rather than clamped
    into plausible-looking data."""
    from fetcher.extractors.whisper import segments_to_chunks

    segments = [
        {"start": 0.0, "end": 2.0, "text": "keep me"},
        {"start": 5.0, "end": 3.0, "text": "ends before it starts"},
        {"start": "x", "end": 6.0, "text": "non-numeric"},
        {"end": 7.0, "text": "missing start"},
        {"start": 8.0, "end": 9.0, "text": "   "},
        {"start": 10.0, "end": 999.0, "text": "runs past the chunk"},
        {"start": 12.0, "end": 13.5, "text": "keep me too"},
    ]

    chunks = segments_to_chunks(segments, offset=0.0, chunk_duration=20.0)

    assert [c["text"] for c in chunks] == ["keep me", "keep me too"]
    assert [c["start"] for c in chunks] == sorted(c["start"] for c in chunks)


def test_stitching_drops_segments_running_past_their_own_chunk() -> None:
    """Whisper hallucinates trailing segments on silence; left in, they overlap the
    next chunk's real speech once offset."""
    from fetcher.extractors.whisper import stitch_chunk_segments

    per_chunk = [
        (
            [
                {"start": 0.0, "end": 10.0, "text": "real speech"},
                {"start": 11.0, "end": 900.0, "text": "hallucinated tail"},
            ],
            600.0,
        ),
        ([{"start": 0.0, "end": 5.0, "text": "next chunk"}], 300.0),
    ]

    chunks = stitch_chunk_segments(per_chunk)

    assert [c["text"] for c in chunks] == ["real speech", "next chunk"]
    assert [c["start"] for c in chunks] == [0.0, 600.0]


def test_probe_duration_measures_the_real_file(medium_mp3) -> None:
    """The stitched timeline is only as good as this number, so it comes from the
    media itself."""
    from fetcher.extractors.whisper import probe_duration

    assert probe_duration(medium_mp3) == pytest.approx(25.0, abs=0.5)
