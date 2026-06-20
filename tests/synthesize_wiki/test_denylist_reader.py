"""Notion-backed denylist reader (R1).

WikiPagesNotionResource.query_rejected() reads the "Wiki Pages" Notion DB,
filters Rejected=true rows, and returns {entity_id: {category, reason}}.
The notion Client is mocked at the import location, mirroring how the
NotionQueueResource pattern is exercised.
"""

from unittest.mock import MagicMock, patch

from orchestrators.defs.synthesize_wiki.resources import WikiPagesNotionResource


def _row(entity_id: str, category: str | None, reason: str | None) -> dict:
    return {
        "properties": {
            "Entity ID": {"rich_text": [{"plain_text": entity_id}]},
            "Reject category": {"select": {"name": category} if category else None},
            "Reject reason": {"rich_text": [{"plain_text": reason}] if reason else []},
        }
    }


def test_query_rejected_returns_denylist_dict():
    res = WikiPagesNotionResource(integration_token="t", wiki_pages_data_source_id="ds")
    client = MagicMock()
    client.data_sources.query.return_value = {
        "results": [
            _row("concept__cli", "generic", "common-knowledge term"),
            _row("tool__claude_code", "already_familiar", "I already know it"),
        ],
        "has_more": False,
    }

    with patch("orchestrators.defs.synthesize_wiki.resources.NotionClient", return_value=client):
        result = res.query_rejected()

    assert result == {
        "concept__cli": {"category": "generic", "reason": "common-knowledge term"},
        "tool__claude_code": {"category": "already_familiar", "reason": "I already know it"},
    }


def test_query_rejected_paginates_until_exhausted():
    """Must scan every page (has_more) — a denylist truncated at a page
    boundary would silently re-admit rejected entities."""
    res = WikiPagesNotionResource(integration_token="t", wiki_pages_data_source_id="ds")
    client = MagicMock()
    client.data_sources.query.side_effect = [
        {"results": [_row("concept__cli", "generic", "a")], "has_more": True, "next_cursor": "c1"},
        {"results": [_row("concept__system_design", "too_broad", "b")], "has_more": False},
    ]

    with patch("orchestrators.defs.synthesize_wiki.resources.NotionClient", return_value=client):
        result = res.query_rejected()

    assert set(result) == {"concept__cli", "concept__system_design"}
    # second call resumed from the first page's cursor
    assert client.data_sources.query.call_count == 2
    assert client.data_sources.query.call_args_list[1].kwargs["start_cursor"] == "c1"
