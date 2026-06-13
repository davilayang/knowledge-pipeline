"""Tests for triage_queued_items.podcast_canonicalize — orchestrator-side
YouTube substitution at canonicalize time. Sub-feature 5a."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from orchestrators.defs.triage_queued_items.podcast_canonicalize import (
    FeedEntry,
    PodcastYouTubeMapping,
    _fetch_playlist_feed,
    _load_mappings,
    _strip_title,
    maybe_redirect_podcast_to_youtube,
)

_SDS_MAPPING = PodcastYouTubeMapping(
    name="super_data_science",
    audio_url_substring="SUPERDATASCIENCEPTYLTD",
    youtube_playlist_id="PLS3615GtilzBoe1fmujEOh8PsKUdnFZco",
    title_strip_pattern=r"^.*?:\s*\d+:\s*",
    match_threshold=80,
)


def _fake_response(xml: str) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.text = xml
    resp.raise_for_status = MagicMock(return_value=None)
    return resp


def _patch_httpx_get(xml: str):
    fake = MagicMock(return_value=_fake_response(xml))
    return patch(
        "orchestrators.defs.triage_queued_items.podcast_canonicalize.httpx.get",
        fake,
    )


def test_load_mappings_parses_yaml(tmp_path: Path) -> None:
    yaml_path = tmp_path / "podcast_youtube_map.yaml"
    yaml_path.write_text(
        """
mappings:
  - name: super_data_science
    audio_url_substring: SUPERDATASCIENCEPTYLTD
    youtube_playlist_id: PLS3615GtilzBoe1fmujEOh8PsKUdnFZco
    title_strip_pattern: '^.*?:\\s*\\d+:\\s*'
    match_threshold: 80
""".strip()
    )

    mappings = _load_mappings(yaml_path)

    assert len(mappings) == 1
    assert isinstance(mappings[0], PodcastYouTubeMapping)
    assert mappings[0].name == "super_data_science"
    assert mappings[0].audio_url_substring == "SUPERDATASCIENCEPTYLTD"
    assert mappings[0].youtube_playlist_id == "PLS3615GtilzBoe1fmujEOh8PsKUdnFZco"
    assert mappings[0].title_strip_pattern == r"^.*?:\s*\d+:\s*"
    assert mappings[0].match_threshold == 80


def test_strip_audio_title_handles_sds_format() -> None:
    audio_title = (
        "Super Data Science: ML & AI Podcast with Jon Krohn: 999: "
        "What's Left to Build When Software Is Free, with Chip Huyen"
    )
    pattern = r"^.*?:\s*\d+:\s*"

    result = _strip_title(audio_title, pattern)

    assert result == "What's Left to Build When Software Is Free, with Chip Huyen"


def test_strip_audio_title_returns_input_when_pattern_does_not_match() -> None:
    audio_title = "Some Podcast Without The Prefix Pattern"
    pattern = r"^.*?:\s*\d+:\s*"

    result = _strip_title(audio_title, pattern)

    assert result == audio_title


_ATOM_FIXTURE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns="http://www.w3.org/2005/Atom">
 <entry>
  <id>yt:video:vi6UILzThgo</id>
  <yt:videoId>vi6UILzThgo</yt:videoId>
  <title>What's Left to Build When Software Is Free (with Chip Huyen)</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=vi6UILzThgo"/>
  <published>2026-06-09T11:00:21+00:00</published>
 </entry>
 <entry>
  <id>yt:video:fVnBlF8JuUk</id>
  <yt:videoId>fVnBlF8JuUk</yt:videoId>
  <title>How This Text-to-Video-Game AI Startup Hit 20M Users (Ep. 997 with Andrey Kurenkov)</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=fVnBlF8JuUk"/>
  <published>2026-06-02T11:00:23+00:00</published>
 </entry>
</feed>"""


def test_fetch_playlist_feed_parses_atom_xml() -> None:
    with _patch_httpx_get(_ATOM_FIXTURE_XML) as fake_get:
        entries = _fetch_playlist_feed("PLS3615GtilzBoe1fmujEOh8PsKUdnFZco")

    assert len(entries) == 2
    assert isinstance(entries[0], FeedEntry)
    assert entries[0].video_id == "vi6UILzThgo"
    assert entries[0].title == "What's Left to Build When Software Is Free (with Chip Huyen)"
    assert entries[0].url == "https://www.youtube.com/watch?v=vi6UILzThgo"
    assert entries[1].video_id == "fVnBlF8JuUk"

    called_url = fake_get.call_args[0][0]
    assert "playlist_id=PLS3615GtilzBoe1fmujEOh8PsKUdnFZco" in called_url
    assert "feeds/videos.xml" in called_url


def test_fuzzy_match_picks_best_entry_above_threshold() -> None:
    feed_xml = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns="http://www.w3.org/2005/Atom">
 <entry>
  <yt:videoId>aaaaaaaaaaa</yt:videoId>
  <title>Stop Throwing Compute at Bad Data (Ep. 995 with Jazmia Henry)</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=aaaaaaaaaaa"/>
 </entry>
 <entry>
  <yt:videoId>vi6UILzThgo</yt:videoId>
  <title>What's Left to Build When Software Is Free (with Chip Huyen)</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=vi6UILzThgo"/>
 </entry>
 <entry>
  <yt:videoId>ccccccccccc</yt:videoId>
  <title>How to Build AI-First Organizations (Ep. 993 with Jacob Miller and Jeremy Mumford)</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=ccccccccccc"/>
 </entry>
</feed>"""
    audio_url = "https://example.com/SUPERDATASCIENCEPTYLTD7992118381.mp3"
    audio_title = (
        "Super Data Science: ML & AI Podcast with Jon Krohn: 999: "
        "What's Left to Build When Software Is Free, with Chip Huyen"
    )

    with _patch_httpx_get(feed_xml):
        result = maybe_redirect_podcast_to_youtube(
            audio_url=audio_url,
            audio_title=audio_title,
            mappings=[_SDS_MAPPING],
        )

    assert result == "https://www.youtube.com/watch?v=vi6UILzThgo"


def test_returns_none_when_no_confident_match() -> None:
    feed_xml = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns="http://www.w3.org/2005/Atom">
 <entry>
  <yt:videoId>xxxxxxxxxxx</yt:videoId>
  <title>How to Build AI-First Organizations (Ep. 993)</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=xxxxxxxxxxx"/>
 </entry>
 <entry>
  <yt:videoId>yyyyyyyyyyy</yt:videoId>
  <title>Stop Throwing Compute at Bad Data (Ep. 995)</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=yyyyyyyyyyy"/>
 </entry>
</feed>"""

    with _patch_httpx_get(feed_xml):
        result = maybe_redirect_podcast_to_youtube(
            audio_url="https://example.com/SUPERDATASCIENCEPTYLTDxxxx.mp3",
            audio_title=(
                "Super Data Science: ML & AI Podcast: 1000: " "Totally Different Episode Title"
            ),
            mappings=[_SDS_MAPPING],
        )

    assert result is None


def test_returns_none_when_no_mapping_matches_audio_url() -> None:
    with _patch_httpx_get("<feed/>") as fake_get:
        result = maybe_redirect_podcast_to_youtube(
            audio_url="https://traffic.libsyn.com/some-other-show/episode.mp3",
            audio_title="Anything",
            mappings=[_SDS_MAPPING],
        )

    assert result is None
    assert fake_get.call_count == 0


def test_sds_999_chip_huyen_redirects_to_youtube_video() -> None:
    """GOLDEN: the load-bearing case this matcher was built for.

    Notion queue row captured 2026-06-10 (DEV DB, page 37bd130d-...):
      Name: Super Data Science: ML & AI Podcast with Jon Krohn: 999: ...
      URL: https://www.podtrac.com/.../SUPERDATASCIENCEPTYLTD7992118381.mp3
    Expected: redirect to https://www.youtube.com/watch?v=vi6UILzThgo
    (Chip Huyen episode, the actual entry[0] in the real SDS playlist feed
    on the day the queue row was captured)."""
    feed_xml = (Path(__file__).parent / "fixtures" / "sds_playlist_feed.xml").read_text()
    audio_url = (
        "https://www.podtrac.com/pts/redirect.mp3/chrt.fm/track/E581B9/"
        "arttrk.com/p/VI4CS/pscrb.fm/rss/p/traffic.megaphone.fm/"
        "SUPERDATASCIENCEPTYLTD7992118381.mp3"
    )
    audio_title = (
        "Super Data Science: ML & AI Podcast with Jon Krohn: 999: "
        "What's Left to Build When Software Is Free, with Chip Huyen"
    )

    with _patch_httpx_get(feed_xml):
        result = maybe_redirect_podcast_to_youtube(
            audio_url=audio_url,
            audio_title=audio_title,
            mappings=[_SDS_MAPPING],
        )

    assert result == "https://www.youtube.com/watch?v=vi6UILzThgo"
