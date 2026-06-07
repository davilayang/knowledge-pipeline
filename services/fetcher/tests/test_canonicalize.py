"""Tests for fetcher.canonicalize."""

from unittest.mock import MagicMock, patch

from fetcher.canonicalize import canonicalize


def _mock_redirect(final_url: str, chain: list[str] | None = None) -> MagicMock:
    response = MagicMock()
    response.url = final_url
    response.history = [MagicMock(url=url) for url in (chain or [])]
    response.raise_for_status = MagicMock()
    return response


def test_strips_utm_params() -> None:
    """utm_* params are removed from the final URL."""
    with patch("fetcher.canonicalize._follow_redirects") as follow_redirects:
        follow_redirects.return_value = _mock_redirect(
            "https://example.com/article?utm_source=twitter&utm_campaign=x&id=42"
        )
        result = canonicalize("https://example.com/article?utm_source=twitter&utm_campaign=x&id=42")

    assert result.canonical_url == "https://example.com/article?id=42"
    assert set(result.params_stripped) == {"utm_source", "utm_campaign"}


def test_follows_redirects() -> None:
    """Click-tracker redirects resolve to the final URL."""
    with patch("fetcher.canonicalize._follow_redirects") as follow_redirects:
        follow_redirects.return_value = _mock_redirect(
            "https://medium.com/the-article",
            chain=["https://t.co/abc"],
        )
        result = canonicalize("https://t.co/abc")

    assert result.canonical_url == "https://medium.com/the-article"
    assert result.redirects_followed == ["https://t.co/abc", "https://medium.com/the-article"]


def test_already_clean_url_unchanged() -> None:
    """A URL with no trackers and no redirects is returned as-is."""
    with patch("fetcher.canonicalize._follow_redirects") as follow_redirects:
        follow_redirects.return_value = _mock_redirect("https://example.com/article")
        result = canonicalize("https://example.com/article")

    assert result.canonical_url == "https://example.com/article"
    assert result.params_stripped == []


def test_input_url_preserved_in_result() -> None:
    """The input URL is always echoed back so callers can compare."""
    with patch("fetcher.canonicalize._follow_redirects") as follow_redirects:
        follow_redirects.return_value = _mock_redirect("https://example.com/x")
        result = canonicalize("https://t.co/abc")

    assert result.input_url == "https://t.co/abc"
