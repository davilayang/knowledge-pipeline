"""Notion-backed denylist reader (R1).

WikiPagesNotionResource.query_rejected() reads the "Wiki Pages" Notion DB,
filters Rejected=true rows, and returns {normalized_title: {category, reason}} —
keyed on the normalised page Title (the entity's canonical name), since the
surrogate Entity ID can't be matched pre-mint. The notion Client is mocked at
the import location, mirroring how the NotionQueueResource pattern is exercised.
"""

from unittest.mock import MagicMock, patch

from orchestrators.defs.synthesize_wiki.resources import WikiPagesNotionResource


def _row(title: str, category: str | None, reason: str | None) -> dict:
    return {
        "properties": {
            "Title": {"title": [{"plain_text": title}]},
            "Reject category": {"select": {"name": category} if category else None},
            "Reject reason": {"rich_text": [{"plain_text": reason}] if reason else []},
        }
    }


def test_query_rejected_returns_denylist_dict():
    res = WikiPagesNotionResource(integration_token="t", wiki_data_source_id="ds")
    client = MagicMock()
    client.data_sources.query.return_value = {
        "results": [
            _row("CLI", "generic", "common-knowledge term"),
            _row("Claude Code", "already_familiar", "I already know it"),
        ],
        "has_more": False,
    }

    with patch("orchestrators.defs.synthesize_wiki.resources.NotionClient", return_value=client):
        result = res.query_rejected()

    # Keys are normalised (lower/trim/collapse-ws) so they match extracted +
    # resolved entity names at synthesis time.
    assert result == {
        "cli": {"category": "generic", "reason": "common-knowledge term"},
        "claude code": {"category": "already_familiar", "reason": "I already know it"},
    }


def test_query_rejected_paginates_until_exhausted():
    """Must scan every page (has_more) — a denylist truncated at a page
    boundary would silently re-admit rejected entities."""
    res = WikiPagesNotionResource(integration_token="t", wiki_data_source_id="ds")
    client = MagicMock()
    client.data_sources.query.side_effect = [
        {"results": [_row("CLI", "generic", "a")], "has_more": True, "next_cursor": "c1"},
        {"results": [_row("System Design", "too_broad", "b")], "has_more": False},
    ]

    with patch("orchestrators.defs.synthesize_wiki.resources.NotionClient", return_value=client):
        result = res.query_rejected()

    assert set(result) == {"cli", "system design"}
    # second call resumed from the first page's cursor
    assert client.data_sources.query.call_count == 2
    assert client.data_sources.query.call_args_list[1].kwargs["start_cursor"] == "c1"
