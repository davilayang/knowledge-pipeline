"""Tests for the URL → enrichment-signals dispatcher.

`enrich_url` runs per-source HTTP calls (YouTube oEmbed, arXiv Atom API,
article HTML meta) and serialises the result as `enrichment_json`. The
contract under test:

- Per-source signals extract what `classify_content_shape` needs (channel
  name, arXiv categories, article description).
- Any HTTP / parse failure collapses to empty signals — never raises. Triage
  must not fail on enrichment.
- `EnrichmentSignals.to_json()` round-trips lossily-but-stably (only
  populated sources serialise).
"""

import json
from unittest.mock import MagicMock, patch

import httpx
from orchestrators.defs.triage_queued_items.enrich import (
    ArticleSignals,
    ArxivSignals,
    EnrichmentSignals,
    YoutubeSignals,
    enrich_url,
)
from orchestrators.defs.triage_queued_items.url_meta import UrlMeta

# ---------------- youtube ----------------


_OEMBED_RESPONSE = {
    "title": "How to ship a thing",
    "author_name": "AI Engineer",
    "author_url": "https://www.youtube.com/@aiengineer",
    "type": "video",
    "provider_name": "YouTube",
}


def _fake_response(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data or {})
    resp.text = json.dumps(json_data) if json_data else ""
    return resp


def test_youtube_signals_extracts_channel_and_title():
    resp = _fake_response(json_data=_OEMBED_RESPONSE)
    with patch("orchestrators.defs.triage_queued_items.enrich.httpx.get", return_value=resp):
        signals = enrich_url("https://www.youtube.com/watch?v=abc123", "YouTube")
    assert signals.youtube == YoutubeSignals(
        channel="AI Engineer",
        title="How to ship a thing",
    )


def test_youtube_signals_returns_empty_on_oembed_http_error():
    with patch(
        "orchestrators.defs.triage_queued_items.enrich.httpx.get",
        side_effect=httpx.ConnectError("offline"),
    ):
        signals = enrich_url("https://www.youtube.com/watch?v=abc123", "YouTube")
    assert signals.youtube == YoutubeSignals()


def test_youtube_signals_returns_empty_on_oembed_4xx():
    resp = _fake_response(status_code=404)
    with patch("orchestrators.defs.triage_queued_items.enrich.httpx.get", return_value=resp):
        signals = enrich_url("https://www.youtube.com/watch?v=missing", "YouTube")
    assert signals.youtube == YoutubeSignals()


# ---------------- arxiv ----------------


_ARXIV_ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2105.04663v1</id>
    <title>
      Sample arXiv Title
    </title>
    <summary>
      Sample abstract body explaining the result.
    </summary>
    <category term="cs.LG" />
    <category term="stat.ML" />
  </entry>
</feed>
"""


def _fake_arxiv_response(text: str = _ARXIV_ATOM_XML, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    return resp


def test_arxiv_signals_extracts_title_abstract_categories():
    resp = _fake_arxiv_response()
    with patch("orchestrators.defs.triage_queued_items.enrich.httpx.get", return_value=resp):
        signals = enrich_url("https://arxiv.org/abs/2105.04663", "arXiv")
    assert signals.arxiv is not None
    assert signals.arxiv.title == "Sample arXiv Title"
    assert signals.arxiv.abstract == "Sample abstract body explaining the result."
    assert signals.arxiv.categories == ("cs.LG", "stat.ML")


def test_arxiv_signals_returns_empty_on_http_error():
    with patch(
        "orchestrators.defs.triage_queued_items.enrich.httpx.get",
        side_effect=httpx.ReadTimeout("export.arxiv.org slow"),
    ):
        signals = enrich_url("https://arxiv.org/abs/2105.04663", "arXiv")
    assert signals.arxiv == ArxivSignals()


def test_arxiv_signals_returns_empty_on_invalid_id():
    """URL doesn't look like an arXiv ID → skip the API entirely."""
    with patch("orchestrators.defs.triage_queued_items.enrich.httpx.get") as fake:
        signals = enrich_url("https://arxiv.org/about", "arXiv")
    assert signals.arxiv == ArxivSignals()
    fake.assert_not_called()


def test_arxiv_signals_returns_empty_on_malformed_xml():
    resp = _fake_arxiv_response(text="<not><valid>xml")
    with patch("orchestrators.defs.triage_queued_items.enrich.httpx.get", return_value=resp):
        signals = enrich_url("https://arxiv.org/abs/2105.04663", "arXiv")
    assert signals.arxiv == ArxivSignals()


# ---------------- article ----------------


def test_article_signals_passes_through_url_meta():
    meta = UrlMeta(
        final_url="https://example.com/post?utm=x",
        title="Hello",
        description="A short description.",
    )
    with patch(
        "orchestrators.defs.triage_queued_items.enrich.fetch_url_meta",
        return_value=meta,
    ):
        signals = enrich_url("https://example.com/post", "Article")
    assert signals.article == ArticleSignals(
        final_url="https://example.com/post?utm=x",
        title="Hello",
        description="A short description.",
    )


def test_article_signals_empty_when_url_meta_returns_nothing():
    meta = UrlMeta(final_url="https://example.com", title=None, description=None)
    with patch(
        "orchestrators.defs.triage_queued_items.enrich.fetch_url_meta",
        return_value=meta,
    ):
        signals = enrich_url("https://example.com", "Article")
    assert signals.article == ArticleSignals(
        final_url="https://example.com",
        title=None,
        description=None,
    )


# ---------------- dispatch ----------------


def test_enrich_url_returns_empty_signals_for_podcast():
    """Phase 2a doesn't enrich podcast URLs — covered by Phase 2b."""
    with patch("orchestrators.defs.triage_queued_items.enrich.httpx.get") as fake:
        signals = enrich_url("https://podtrac.example.com/show.mp3", "Podcast")
    assert signals == EnrichmentSignals()
    fake.assert_not_called()


def test_enrich_url_returns_empty_signals_for_other():
    with patch("orchestrators.defs.triage_queued_items.enrich.httpx.get") as fake:
        signals = enrich_url("https://news.ycombinator.com/item?id=1", "Other")
    assert signals == EnrichmentSignals()
    fake.assert_not_called()


def test_enrich_url_never_raises_on_unexpected_error():
    """Defensive: any unexpected exception inside a per-source helper
    collapses to empty signals. Triage must keep moving even if enrichment
    blows up."""
    with patch(
        "orchestrators.defs.triage_queued_items.enrich._youtube_signals",
        side_effect=RuntimeError("unexpected"),
    ):
        signals = enrich_url("https://youtube.com/watch?v=abc", "YouTube")
    assert signals == EnrichmentSignals()


# ---------------- serialization ----------------


def test_empty_signals_serialises_to_empty_object():
    assert EnrichmentSignals().to_json() == "{}"


def test_signals_serialise_only_populated_sources():
    signals = EnrichmentSignals(youtube=YoutubeSignals(channel="X", title="Y"))
    payload = json.loads(signals.to_json())
    assert payload == {"youtube": {"channel": "X", "title": "Y"}}
    assert "arxiv" not in payload
    assert "article" not in payload


def test_signals_serialise_categories_as_list():
    """tuple → JSON list (json.dumps does this by default; lock the shape)."""
    signals = EnrichmentSignals(
        arxiv=ArxivSignals(title="t", abstract="a", categories=("cs.LG", "stat.ML"))
    )
    payload = json.loads(signals.to_json())
    assert payload["arxiv"]["categories"] == ["cs.LG", "stat.ML"]
