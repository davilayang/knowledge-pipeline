"""Tests for the YouTube watch-page upload-date extractor."""

from fetcher.extractors import youtube_watch


def test_parse_upload_date_from_microformat():
    # YouTube's watch page server-renders the upload date in its SEO microformat;
    # pull it out with a regex (no JS, no API key).
    html = 'foo {"uploadDate":"2026-01-20T22:00:25-08:00","publishDate":"..."} bar'
    assert youtube_watch.parse_upload_date(html) == "2026-01-20T22:00:25-08:00"


def test_parse_upload_date_absent():
    # The consent-wall variant (served to data-center IPs) strips the field →
    # None, so the caller leaves the date absent rather than inventing one.
    assert youtube_watch.parse_upload_date("<html>no date here</html>") is None
