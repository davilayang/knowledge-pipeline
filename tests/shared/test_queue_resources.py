from unittest.mock import MagicMock, patch

from orchestrators.defs.shared.queue_resources import NotionQueueResource


def _resource() -> NotionQueueResource:
    return NotionQueueResource(integration_token="t", queue_db_id="d", queue_data_source_id="s")


def test_get_page_comments_extracts_text_author_time():
    res = _resource()
    client = MagicMock()
    client.comments.list.return_value = {
        "results": [
            {
                "rich_text": [{"plain_text": "focus on the "}, {"plain_text": "chunking"}],
                "created_by": {"id": "u1"},
                "created_time": "2026-06-28T10:00:00.000Z",
            },
            {"rich_text": [], "created_by": {"id": "u1"}, "created_time": "t2"},
        ],
        "has_more": False,
    }
    with patch.object(NotionQueueResource, "_client", return_value=client):
        out = res.get_page_comments("p1")
    assert out == [
        {"author": "u1", "text": "focus on the chunking", "created_at": "2026-06-28T10:00:00.000Z"}
    ]
    client.comments.list.assert_called_once_with(block_id="p1")


def test_get_page_comments_paginates():
    res = _resource()
    client = MagicMock()
    page1 = {
        "results": [
            {
                "rich_text": [{"plain_text": "first comment"}],
                "created_by": {"id": "u1"},
                "created_time": "2026-06-28T09:00:00.000Z",
            }
        ],
        "has_more": True,
        "next_cursor": "c2",
    }
    page2 = {
        "results": [
            {
                "rich_text": [{"plain_text": "second comment"}],
                "created_by": {"id": "u2"},
                "created_time": "2026-06-28T10:00:00.000Z",
            }
        ],
        "has_more": False,
    }
    client.comments.list.side_effect = [page1, page2]
    with patch.object(NotionQueueResource, "_client", return_value=client):
        out = res.get_page_comments("p1")
    assert len(out) == 2
    assert out[0]["text"] == "first comment"
    assert out[1]["text"] == "second comment"
    # Second call must carry the cursor from the first page's next_cursor.
    client.comments.list.assert_called_with(block_id="p1", start_cursor="c2")


def test_query_for_extract_filter_claims_book_chapter_rows():
    """The extract sensor selects on Content Type, so a type missing from
    `SUPPORTED_CONTENT_TYPES` strands its rows at Status=Fetching with no error.
    Assert the built Notion filter — the tuple alone would not prove the clause
    reaches the query."""
    from orchestrators.defs.fetch_extract_queue.def_config import SUPPORTED_CONTENT_TYPES

    client = MagicMock()
    client.data_sources.query.return_value = {"results": []}
    with patch.object(NotionQueueResource, "_client", return_value=client):
        _resource().query_for_extract(page_size=10, supported_content_types=SUPPORTED_CONTENT_TYPES)

    sent = client.data_sources.query.call_args.kwargs["filter"]
    type_clauses = sent["and"][1]["or"]
    assert {"property": "Content Type", "select": {"equals": "book_chapter"}} in type_clauses


def _page_named(name: str) -> dict:
    return {"properties": {"Name": {"title": [{"plain_text": name}] if name else []}}}


def test_seed_name_leaves_a_title_the_user_chose():
    """Seeding is the first-touch rule triage already applies to every other
    content type: fill a blank Name, never replace one that is already set."""
    res = _resource()
    client = MagicMock()
    client.pages.retrieve.return_value = _page_named("My own title for this chapter")
    with patch.object(NotionQueueResource, "_client", return_value=client):
        res.seed_name("p1", "Evals for AI Engineers — Chapter 1. Introduction")
    client.pages.update.assert_not_called()


def test_seed_name_replaces_notions_own_placeholder():
    """`New queued page` is what Notion puts on a row created in the database
    rather than captured, so it is blank for seeding purposes — the three
    chapters that parked in production all carried exactly this."""
    res = _resource()
    client = MagicMock()
    client.pages.retrieve.return_value = _page_named("New queued page")
    with patch.object(NotionQueueResource, "_client", return_value=client):
        res.seed_name("p1", "Evals for AI Engineers — Chapter 1. Introduction")
    client.pages.update.assert_called_once()
    props = client.pages.update.call_args.kwargs["properties"]
    assert props["Name"]["title"][0]["text"]["content"] == (
        "Evals for AI Engineers — Chapter 1. Introduction"
    )
