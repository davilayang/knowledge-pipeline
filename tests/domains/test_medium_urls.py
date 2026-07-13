"""Tests for the canonical Medium host identity."""

from domains.medium_urls import MEDIUM_DOMAINS, is_medium_url


def test_is_medium_url_known_publication_hosts() -> None:
    assert is_medium_url("https://medium.com/@a/title-abc123def456") is True
    assert is_medium_url("https://towardsdatascience.com/title-abc123def456") is True
    assert is_medium_url("https://www.towardsdatascience.com/title-abc") is True  # www stripped


def test_is_medium_url_author_subdomain() -> None:
    # medium.com's author subdomains are Medium-hosted too.
    assert is_medium_url("https://pravash-techie.medium.com/title-abc123def456") is True


def test_is_medium_url_rejects_non_medium_and_malformed() -> None:
    assert is_medium_url("https://example.com/post-abc123def456") is False
    assert is_medium_url("mailto:x@y.com") is False
    assert is_medium_url("http://[malformed") is False  # never raises


def test_medium_domains_seed_set() -> None:
    assert "medium.com" in MEDIUM_DOMAINS
    assert "towardsdatascience.com" in MEDIUM_DOMAINS
