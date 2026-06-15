"""Tests for the YouTube handler's transcript_api tier + structurer integration."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


from fetcher.extractors._cloud_chain import StructurerChainFailed
from fetcher.handlers import youtube


_VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _make_ctx(
    *,
    structurer_enabled: bool = False,
    socks5_url: str = "",
    rapidapi_key: str | None = None,
) -> MagicMock:
    ctx = MagicMock()
    ctx.openai_api_key = "openai-key"
    ctx.ollama_api_key = "ollama-key"
    ctx.youtube_structurer_enabled = structurer_enabled
    ctx.socks5_url = socks5_url
    ctx.rapidapi_key = rapidapi_key
    ctx.http_client = MagicMock()
    return ctx


def _fake_snippets() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(text="hello world this is the first segment", start=0.0, duration=2.5),
        SimpleNamespace(text="and this is the second segment", start=2.5, duration=2.0),
    ]


def _patch_transcript_api(snippets):
    """Returns a context manager that patches YouTubeTranscriptApi.fetch."""
    api_instance = MagicMock()
    api_instance.fetch.return_value = SimpleNamespace(snippets=snippets)
    api_cls = MagicMock(return_value=api_instance)
    return patch("youtube_transcript_api.YouTubeTranscriptApi", api_cls)


def _patch_oembed(title="Title", author="Channel"):
    from fetcher.extractors.oembed import YouTubeMetadata

    return patch(
        "fetcher.handlers.youtube.oembed_extractor.youtube_metadata",
        AsyncMock(return_value=YouTubeMetadata(title=title, author=author, source_url=_VIDEO_URL)),
    )


async def test_transcript_api_tier_persists_chunks_sidecar_even_when_structurer_disabled() -> None:
    """Raw chunks (text + start + duration) ride along in metadata for downstream
    consumers (future frame alignment, debugging, re-structuring)."""
    ctx = _make_ctx(structurer_enabled=False)

    with _patch_transcript_api(_fake_snippets()), _patch_oembed():
        result = await youtube._transcript_api_tier(ctx, _VIDEO_URL)

    assert result.status == 200
    assert "chunks" in result.metadata
    chunks = result.metadata["chunks"]
    assert chunks == [
        {"text": "hello world this is the first segment", "start": 0.0, "duration": 2.5},
        {"text": "and this is the second segment", "start": 2.5, "duration": 2.0},
    ]


async def test_youtube_structurer_fires_when_flag_enabled() -> None:
    """Flag on + chain succeeds → returned content is the STRUCTURED markdown;
    extra_tier_log carries a transcript_structurer entry the cascade can append."""
    ctx = _make_ctx(structurer_enabled=True)
    structured = "**Host:** Hello, world.\n\n**Guest:** Reply.\n"

    with (
        _patch_transcript_api(_fake_snippets()),
        _patch_oembed(title="My Show", author="The Channel"),
        patch(
            "fetcher.handlers.youtube.transcript_structurer.get_chain",
            return_value=[MagicMock()],  # non-empty → structurer runs
        ),
        patch(
            "fetcher.handlers.youtube.transcript_structurer.structure_transcript",
            new_callable=AsyncMock,
        ) as struct_mock,
    ):
        struct_mock.return_value = (
            structured,
            "structurer:gemma4:31b",
            {"provider": "ollama", "model": "gemma4:31b", "tokens_in": 100, "tokens_out": 80},
        )

        result = await youtube._transcript_api_tier(ctx, _VIDEO_URL)

    assert structured in result.content
    assert len(result.extra_tier_log) == 1
    entry = result.extra_tier_log[0]
    assert entry.tier == "transcript_structurer"
    assert entry.error is None
    # entry.chars reflects the structurer's own output (the body), not the
    # final header+body markdown handed back as result.content.
    assert entry.chars == len(structured)
    assert result.metadata["structurer_tier"] == "structurer:gemma4:31b"
    assert result.metadata["structurer_usage"]["model"] == "gemma4:31b"


async def test_youtube_structurer_falls_back_to_raw_on_chain_failure() -> None:
    """Structurer chain dies → handler returns the raw transcript markdown,
    extra_tier_log records the failure so operators can see what happened."""
    ctx = _make_ctx(structurer_enabled=True)

    with (
        _patch_transcript_api(_fake_snippets()),
        _patch_oembed(),
        patch(
            "fetcher.handlers.youtube.transcript_structurer.get_chain",
            return_value=[MagicMock()],
        ),
        patch(
            "fetcher.handlers.youtube.transcript_structurer.structure_transcript",
            new_callable=AsyncMock,
        ) as struct_mock,
    ):
        struct_mock.side_effect = StructurerChainFailed("upstream timeout", retryable=True)

        result = await youtube._transcript_api_tier(ctx, _VIDEO_URL)

    # Raw transcript text from the fake snippets must still be present.
    assert "hello world this is the first segment" in result.content
    assert len(result.extra_tier_log) == 1
    entry = result.extra_tier_log[0]
    assert entry.tier == "transcript_structurer"
    assert entry.error_kind == "exception"
    assert "upstream timeout" in (entry.detail or "")
    # No structurer_tier metadata when we fell back.
    assert "structurer_tier" not in result.metadata


async def test_youtube_structurer_skipped_when_flag_disabled() -> None:
    """Flag off → structurer module not even called; behaviour matches today."""
    ctx = _make_ctx(structurer_enabled=False)

    with (
        _patch_transcript_api(_fake_snippets()),
        _patch_oembed(),
        patch(
            "fetcher.handlers.youtube.transcript_structurer.structure_transcript",
            new_callable=AsyncMock,
        ) as struct_mock,
    ):
        result = await youtube._transcript_api_tier(ctx, _VIDEO_URL)

    struct_mock.assert_not_awaited()
    assert result.extra_tier_log == []
    assert "structurer_tier" not in result.metadata


async def test_transcript_api_uses_socks5_proxy_when_configured() -> None:
    """YouTube data-center IP-blocks the transcript API on cloud hosts. Wire
    ctx.socks5_url into the client so the call exits via the Tailscale proxy
    on residential IP — same pattern the article handler uses."""
    ctx = _make_ctx(socks5_url="socks5://192.168.1.10:1080")
    api_instance = MagicMock()
    api_instance.fetch.return_value = SimpleNamespace(snippets=_fake_snippets())
    api_cls = MagicMock(return_value=api_instance)

    with (
        patch("youtube_transcript_api.YouTubeTranscriptApi", api_cls),
        patch("youtube_transcript_api.proxies.GenericProxyConfig") as proxy_cls,
        _patch_oembed(),
    ):
        proxy_instance = MagicMock(name="proxy_config")
        proxy_cls.return_value = proxy_instance
        await youtube._transcript_api_tier(ctx, _VIDEO_URL)

    proxy_cls.assert_called_once_with(
        http_url="socks5://192.168.1.10:1080",
        https_url="socks5://192.168.1.10:1080",
    )
    api_cls.assert_called_once_with(proxy_config=proxy_instance)


async def test_transcript_api_surfaces_ipblocked_in_detail() -> None:
    """IpBlocked from cloud-host IPs shouldn't masquerade as a generic
    'empty' tier. The handler surfaces the exception class name in
    RawTierResult.detail so the cascade tier_log shows operators *why*
    the tier failed — proxy config, not 'no transcript exists'."""
    from youtube_transcript_api import IpBlocked

    ctx = _make_ctx()
    api_instance = MagicMock()
    api_instance.fetch.side_effect = IpBlocked("cloud IP blocked")
    api_cls = MagicMock(return_value=api_instance)

    with patch("youtube_transcript_api.YouTubeTranscriptApi", api_cls):
        result = await youtube._transcript_api_tier(ctx, _VIDEO_URL)

    assert result.content == ""
    assert result.status == 0
    assert result.detail is not None
    assert "IpBlocked" in result.detail


async def test_transcript_api_no_proxy_when_socks5_unset() -> None:
    """Empty ctx.socks5_url → instantiate bare (local dev / tests). No
    GenericProxyConfig import overhead, no surprise routing."""
    ctx = _make_ctx(socks5_url="")
    api_instance = MagicMock()
    api_instance.fetch.return_value = SimpleNamespace(snippets=_fake_snippets())
    api_cls = MagicMock(return_value=api_instance)

    with patch("youtube_transcript_api.YouTubeTranscriptApi", api_cls), _patch_oembed():
        await youtube._transcript_api_tier(ctx, _VIDEO_URL)

    api_cls.assert_called_once_with(proxy_config=None)


async def test_youtube_structurer_skipped_when_chain_empty() -> None:
    """No structurer chain entries (missing config) → skip structurer, return raw."""
    ctx = _make_ctx(structurer_enabled=True)

    with (
        _patch_transcript_api(_fake_snippets()),
        _patch_oembed(),
        patch("fetcher.handlers.youtube.transcript_structurer.get_chain", return_value=[]),
        patch(
            "fetcher.handlers.youtube.transcript_structurer.structure_transcript",
            new_callable=AsyncMock,
        ) as struct_mock,
    ):
        result = await youtube._transcript_api_tier(ctx, _VIDEO_URL)

    struct_mock.assert_not_awaited()
    assert result.extra_tier_log == []


# ---------------------------------------------------------------------------
# rapidapi_captions tier (paid fallback when free transcript_api fails)
# ---------------------------------------------------------------------------


def _fake_captions_chunks() -> list[dict]:
    """Shape that rapidapi.youtube_captions.fetch_captions returns —
    already-mapped to text/start/duration so chunks_to_markdown consumes
    it unchanged."""
    return [
        {"text": "hello world this is the first segment", "start": 0.0, "duration": 2.5},
        {"text": "and this is the second segment", "start": 2.5, "duration": 2.0},
    ]


async def test_rapidapi_captions_tier_produces_same_markdown_shape() -> None:
    """When rapidapi_captions runs, the output markdown header/body
    matches what the free transcript_api tier produces — only the
    chunk source differs. Same metadata['chunks'] key, same header,
    same body formatter."""
    ctx = _make_ctx(rapidapi_key="rapid-key")

    with (
        patch(
            "fetcher.handlers.youtube.rapidapi_captions_extractor.fetch_captions",
            new_callable=AsyncMock,
        ) as fetch_mock,
        _patch_oembed(title="My Show", author="The Channel"),
    ):
        fetch_mock.return_value = _fake_captions_chunks()
        result = await youtube._rapidapi_captions_tier(ctx, _VIDEO_URL)

    fetch_mock.assert_awaited_once()
    call_kwargs = fetch_mock.await_args.kwargs
    assert call_kwargs["video_id"] == "dQw4w9WgXcQ"
    assert call_kwargs["api_key"] == "rapid-key"

    assert result.status == 200
    assert "hello world this is the first segment" in result.content
    assert "**Channel:** The Channel" in result.content
    assert result.metadata["chunks"] == _fake_captions_chunks()


async def test_rapidapi_captions_tier_skips_when_no_key() -> None:
    """No RAPIDAPI_KEY → skip without calling upstream. Cascade reads
    detail to distinguish from a network failure."""
    ctx = _make_ctx(rapidapi_key=None)

    with patch(
        "fetcher.handlers.youtube.rapidapi_captions_extractor.fetch_captions",
    ) as fetch_mock:
        result = await youtube._rapidapi_captions_tier(ctx, _VIDEO_URL)

    fetch_mock.assert_not_called()
    assert result.status == 0
    assert result.content == ""
    assert "RAPIDAPI_KEY not configured" in (result.detail or "")


async def test_rapidapi_captions_tier_surfaces_extractor_error_in_detail() -> None:
    """Extractor raises ValueError (HTTP 403, empty list, etc.) →
    handler maps to RawTierResult.detail so cascade tier_log shows
    *why* the paid fallback also failed."""
    ctx = _make_ctx(rapidapi_key="rapid-key")

    with patch(
        "fetcher.handlers.youtube.rapidapi_captions_extractor.fetch_captions",
        new_callable=AsyncMock,
    ) as fetch_mock:
        fetch_mock.side_effect = ValueError(
            "RapidAPI youtube-data16: empty caption list for video_id=dQw4w9WgXcQ lang=en"
        )
        result = await youtube._rapidapi_captions_tier(ctx, _VIDEO_URL)

    assert result.status == 0
    assert result.content == ""
    assert "empty caption list" in (result.detail or "")


async def test_rapidapi_captions_tier_runs_structurer_when_flag_enabled() -> None:
    """Symmetry with transcript_api tier — same _finalize_chunks helper
    means structurer fires for both. Confirms the refactor didn't
    accidentally route around the structurer for the new tier."""
    ctx = _make_ctx(structurer_enabled=True, rapidapi_key="rapid-key")
    structured = "**Host:** Hello.\n\n**Guest:** Reply.\n"

    with (
        patch(
            "fetcher.handlers.youtube.rapidapi_captions_extractor.fetch_captions",
            new_callable=AsyncMock,
            return_value=_fake_captions_chunks(),
        ),
        _patch_oembed(title="My Show", author="The Channel"),
        patch(
            "fetcher.handlers.youtube.transcript_structurer.get_chain",
            return_value=[MagicMock()],
        ),
        patch(
            "fetcher.handlers.youtube.transcript_structurer.structure_transcript",
            new_callable=AsyncMock,
        ) as struct_mock,
    ):
        struct_mock.return_value = (
            structured,
            "structurer:gemma4:31b",
            {"provider": "ollama", "model": "gemma4:31b", "tokens_in": 100, "tokens_out": 80},
        )
        result = await youtube._rapidapi_captions_tier(ctx, _VIDEO_URL)

    assert structured in result.content
    assert len(result.extra_tier_log) == 1
    assert result.extra_tier_log[0].tier == "transcript_structurer"
    assert result.metadata["structurer_tier"] == "structurer:gemma4:31b"


def test_handler_registers_transcript_api_before_rapidapi_captions() -> None:
    """Free tier runs first; paid fallback only fires when transcript_api
    returned empty AND the request opted into paid tiers."""
    names = [t.name for t in youtube.TIERS]
    assert names == ["transcript_api", "rapidapi_captions"]
    assert youtube.TIERS[0].cost == "free"
    assert youtube.TIERS[1].cost == "paid"
    assert youtube.TIERS[1].rate_limit_key == "rapidapi"
