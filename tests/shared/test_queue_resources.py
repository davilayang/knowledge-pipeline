from unittest.mock import MagicMock, patch

from orchestrators.defs.shared.queue_resources import NotionQueueResource


def _resource() -> NotionQueueResource:
    return NotionQueueResource(
        integration_token="t", queue_db_id="d", queue_data_source_id="s"
    )


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
