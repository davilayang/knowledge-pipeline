"""Unit tests for the YouTube fetcher — URL classification and video ID extraction.

Network calls (YouTubeTranscriptApi.fetch, httpx oEmbed) are integration-level
and not covered here. URL helpers are pure-Python and fully testable.
"""

from orchestrators.defs.extract_complex_contents.fetchers import youtube

# -------- is_youtube_url --------


def test_is_youtube_url_recognises_canonical_form():
    assert youtube.is_youtube_url("https://www.youtube.com/watch?v=abcdefghijk")


def test_is_youtube_url_recognises_short_form():
    assert youtube.is_youtube_url("https://youtu.be/abcdefghijk")


def test_is_youtube_url_rejects_non_youtube():
    assert not youtube.is_youtube_url("https://example.com/abc")


def test_is_youtube_url_rejects_youtube_without_valid_id():
    assert not youtube.is_youtube_url("https://www.youtube.com/watch?v=tooshort")


def test_is_youtube_url_recognises_mobile_form():
    assert youtube.is_youtube_url("https://m.youtube.com/watch?v=abcdefghijk")


# -------- extract_video_id --------


def test_extract_video_id_handles_short_url():
    assert youtube.extract_video_id("https://youtu.be/abcdefghijk") == "abcdefghijk"


def test_extract_video_id_rejects_malformed():
    assert youtube.extract_video_id("https://www.youtube.com/watch?v=xx") is None


def test_extract_video_id_handles_canonical_url():
    assert youtube.extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_video_id_handles_shorts_url():
    assert youtube.extract_video_id("https://www.youtube.com/shorts/abcdefghijk") == "abcdefghijk"


def test_extract_video_id_handles_embed_url():
    assert youtube.extract_video_id("https://www.youtube.com/embed/abcdefghijk") == "abcdefghijk"
