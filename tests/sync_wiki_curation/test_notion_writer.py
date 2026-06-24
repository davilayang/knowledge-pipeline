"""Producer-write side of WikiPagesNotionResource (S1).

list_pages() scans the "Wiki Pages" DB once (paginated) and returns a ref per
existing row — page_id + the Entity ID / Normalized Name producer columns — so
the push can decide create-vs-update. upsert_page() creates or updates a single
row's producer columns. The Notion Client is mocked at the import location.
"""

from unittest.mock import MagicMock, patch

import pytest
from orchestrators.defs.sync_wiki_curation.resources import (
    NotionPageRef,
    WikiPagesNotionResource,
)


class _RateLimited(Exception):
    status = 429
    headers = {"retry-after": "0"}


def test_request_retries_on_rate_limit():
    """A 429 is retried (honouring Retry-After) so a ~150-row push survives
    Notion's ~3 req/s ceiling instead of dying mid-run."""
    res = WikiPagesNotionResource(
        integration_token="t", wiki_pages_data_source_id="ds", min_request_interval_s=0.0
    )
    attempts = []

    def fn(**kw):
        attempts.append(1)
        if len(attempts) == 1:
            raise _RateLimited("rate limited")
        return {"ok": True}

    assert res._request(fn) == {"ok": True}
    assert len(attempts) == 2  # failed once, retried, succeeded


def test_request_reraises_non_rate_limit_immediately():
    res = WikiPagesNotionResource(
        integration_token="t", wiki_pages_data_source_id="ds", min_request_interval_s=0.0
    )

    def fn(**kw):
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        res._request(fn)


def _row(
    page_id: str, entity_id: str, page_status: str = "active", *, archived: bool = False
) -> dict:
    return {
        "id": page_id,
        "archived": archived,
        "properties": {
            "Entity ID": {"rich_text": [{"plain_text": entity_id}] if entity_id else []},
            "Page status": {"select": {"name": page_status} if page_status else None},
        },
    }


def test_list_pages_returns_refs_paginated():
    res = WikiPagesNotionResource(integration_token="t", wiki_pages_data_source_id="ds")
    client = MagicMock()
    client.data_sources.query.side_effect = [
        {
            "results": [_row("p1", "e_aaa", "active")],
            "has_more": True,
            "next_cursor": "c1",
        },
        {"results": [_row("p2", "e_bbb", "orphaned")], "has_more": False},
    ]

    with patch("orchestrators.defs.sync_wiki_curation.resources.NotionClient", return_value=client):
        refs = res.list_pages()

    assert refs == [
        NotionPageRef(page_id="p1", entity_id="e_aaa", page_status="active"),
        NotionPageRef(page_id="p2", entity_id="e_bbb", page_status="orphaned"),
    ]
    # second call resumed from the first page's cursor
    assert client.data_sources.query.call_count == 2
    assert client.data_sources.query.call_args_list[1].kwargs["start_cursor"] == "c1"


def test_list_pages_skips_archived_rows():
    """Notion delete = archive (trash), not hard delete. An archived row must
    read as absent so the push doesn't resurrect a page the curator deleted."""
    res = WikiPagesNotionResource(integration_token="t", wiki_pages_data_source_id="ds")
    client = MagicMock()
    client.data_sources.query.return_value = {
        "results": [
            _row("p1", "e_aaa"),
            _row("p2", "e_bbb", archived=True),
            {**_row("p3", "e_ccc"), "in_trash": True},
        ],
        "has_more": False,
    }

    with patch("orchestrators.defs.sync_wiki_curation.resources.NotionClient", return_value=client):
        refs = res.list_pages()

    assert [r.entity_id for r in refs] == ["e_aaa"]


def test_upsert_page_creates_when_no_page_id():
    res = WikiPagesNotionResource(integration_token="t", wiki_pages_data_source_id="ds")
    client = MagicMock()
    client.pages.create.return_value = {"id": "new_page"}
    props = {"Title": {"title": [{"text": {"content": "Claude Max"}}]}}

    with patch("orchestrators.defs.sync_wiki_curation.resources.NotionClient", return_value=client):
        page_id = res.upsert_page(properties=props)

    assert page_id == "new_page"
    client.pages.create.assert_called_once_with(
        parent={"type": "data_source_id", "data_source_id": "ds"},
        properties=props,
    )
    client.pages.update.assert_not_called()


def test_upsert_page_updates_when_page_id_given():
    res = WikiPagesNotionResource(integration_token="t", wiki_pages_data_source_id="ds")
    client = MagicMock()
    client.pages.update.return_value = {"id": "p1"}
    props = {"num_sources": {"number": 3}}

    with patch("orchestrators.defs.sync_wiki_curation.resources.NotionClient", return_value=client):
        page_id = res.upsert_page(properties=props, page_id="p1")

    assert page_id == "p1"
    client.pages.update.assert_called_once_with(page_id="p1", properties=props)
    client.pages.create.assert_not_called()


def test_fetch_property_names_returns_schema_keys():
    """The push reads the live schema once to fail loud on drift (a human
    renaming/removing a producer column) rather than writing garbage."""
    res = WikiPagesNotionResource(integration_token="t", wiki_pages_data_source_id="ds")
    client = MagicMock()
    client.data_sources.retrieve.return_value = {
        "properties": {
            "Title": {"type": "title"},
            "Entity ID": {"type": "rich_text"},
            "Rejected": {"type": "checkbox"},
        }
    }

    with patch("orchestrators.defs.sync_wiki_curation.resources.NotionClient", return_value=client):
        names = res.fetch_property_names()

    assert names == {"Title", "Entity ID", "Rejected"}
    client.data_sources.retrieve.assert_called_once_with(data_source_id="ds")
