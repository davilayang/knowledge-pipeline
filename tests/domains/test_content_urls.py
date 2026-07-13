"""Tests for the shared URL → content-type classifier."""

import pytest
from domains.content_urls import classify_url_type


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=abc", "youtube"),
        ("https://youtu.be/abc", "youtube"),
        ("https://music.youtube.com/watch?v=abc", "youtube"),
        ("https://arxiv.org/abs/2401.00001", "arxiv"),
        ("https://arxiv.org/pdf/2401.00001v2.pdf", "arxiv"),  # arxiv beats file_pdf
        ("https://arxiv.org/about", "article"),  # non-paper arxiv page → article (ID-based, not host)
        ("https://arxiv.org/list/cs.AI/recent", "article"),
        ("https://github.com/chio-labs/sqlbuild", "github"),
        ("https://gist.github.com/user/abc", "github"),
        ("https://www.facebook.com/openai/posts/123", "facebook"),
        ("https://fb.watch/abc", "facebook"),
        ("https://example.com/paper.pdf", "file_pdf"),
        ("https://podtrac.com/ep/episode.mp3", "file_audio"),
        ("https://zencastr.com/ep/show.mp4", "file_audio"),  # .mp4 is audio/av now
        ("https://cdn.example.com/ep/episode.opus", "file_audio"),
        ("https://cdn.example.com/ep/episode.flac", "file_audio"),
        ("https://example.com/some-article", "article"),
        ("https://medium.com/@a/post-123", "medium"),
        ("https://towardsdatascience.com/title-abc", "medium"),
        ("https://pravash-techie.medium.com/title-abc", "medium"),  # author subdomain
        ("http://[malformed", "article"),  # malformed URL never raises
    ],
)
def test_classify_url_type(url: str, expected: str) -> None:
    assert classify_url_type(url) == expected
