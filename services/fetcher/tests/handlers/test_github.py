"""Tests for the github handler (raw README.md fetch)."""

from unittest.mock import AsyncMock, MagicMock

from fetcher.handlers import github


def test_github_matches_repo_root_only() -> None:
    assert github.matches("https://github.com/chio-labs/sqlbuild") is True
    assert github.matches("https://github.com/openai/openai-python") is True
    assert github.matches("https://github.com/openai/openai-python/") is True  # trailing slash
    # Not a repo-root (exactly <org>/<repo>) → falls through to other handlers:
    assert github.matches("https://github.com/openai") is False  # profile / org
    assert github.matches("https://github.com/org/repo/blob/main/paper.pdf") is False  # → pdf
    assert github.matches("https://github.com/org/repo/tree/main") is False
    # Not the github.com host:
    assert github.matches("https://gist.github.com/user/abc") is False
    assert github.matches("https://docs.github.com/en/rest") is False
    assert github.matches("https://raw.githubusercontent.com/a/b/main/README.md") is False
    assert github.matches("https://example.com/repo") is False
    assert github.matches("mailto:x@y.com") is False


def test_github_single_free_readme_tier() -> None:
    assert [(t.name, t.cost) for t in github.TIERS] == [("github_readme", "free")]


async def test_github_readme_tier_fetches_raw_readme_at_head() -> None:
    ctx = MagicMock()
    ctx.upstream_timeout_s = 30
    resp = MagicMock(status_code=200, text="# sqlbuild\n\nA build tool.")
    ctx.http_client.get = AsyncMock(return_value=resp)

    from fetcher.handlers.github import _readme_fetch

    result = await _readme_fetch(ctx, "https://github.com/chio-labs/sqlbuild")

    assert result.content == "# sqlbuild\n\nA build tool."
    assert result.status == 200
    # HEAD resolves the default branch; README.md at the repo root.
    assert (
        ctx.http_client.get.call_args.args[0]
        == "https://raw.githubusercontent.com/chio-labs/sqlbuild/HEAD/README.md"
    )


async def test_github_readme_tier_fails_on_missing_readme() -> None:
    # No README.md at HEAD (or private / nonexistent) → fail the tier so the item
    # hits the error-state for manual paste, not return the 404 body.
    ctx = MagicMock()
    ctx.upstream_timeout_s = 30
    resp = MagicMock(status_code=404, text="404: Not Found")
    ctx.http_client.get = AsyncMock(return_value=resp)

    from fetcher.handlers.github import _readme_fetch

    result = await _readme_fetch(ctx, "https://github.com/org/no-readme-repo")

    assert result.content == ""
    assert result.status == 404
