"""Unit tests for the arXiv fetcher — URL classification, ID extraction,
and LlamaParse helpers.

Network calls to arxiv.Client are integration-level and not covered here.
LlamaParse helpers are covered by mocking httpx.Client at the import site.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from orchestrators.defs.extract_complex_contents.fetchers import arxiv

# -------- is_arxiv_url --------


def test_is_arxiv_url_canonical_abs():
    assert arxiv.is_arxiv_url("https://arxiv.org/abs/2310.06770")


def test_is_arxiv_url_canonical_pdf():
    assert arxiv.is_arxiv_url("https://arxiv.org/pdf/2310.06770.pdf")


def test_is_arxiv_url_rejects_other_host():
    assert not arxiv.is_arxiv_url("https://example.com/abs/2310.06770")


def test_is_arxiv_url_rejects_non_paper_path():
    assert not arxiv.is_arxiv_url("https://arxiv.org/help")


def test_is_arxiv_url_accepts_version_suffix():
    assert arxiv.is_arxiv_url("https://arxiv.org/abs/2310.06770v2")


def test_is_arxiv_url_accepts_old_style_id():
    assert arxiv.is_arxiv_url("https://arxiv.org/abs/hep-th/0001001")


# -------- extract_arxiv_id --------


def test_extract_arxiv_id_strips_version():
    assert arxiv.extract_arxiv_id("https://arxiv.org/abs/2310.06770v2") == "2310.06770"


def test_extract_arxiv_id_handles_old_format():
    assert arxiv.extract_arxiv_id("https://arxiv.org/abs/hep-th/0001001") == "hep-th/0001001"


def test_extract_arxiv_id_raises_on_malformed():
    with pytest.raises(ValueError):
        arxiv.extract_arxiv_id("https://arxiv.org/foo/bar")


def test_extract_arxiv_id_strips_pdf_suffix():
    assert arxiv.extract_arxiv_id("https://arxiv.org/pdf/2310.06770.pdf") == "2310.06770"


def test_extract_arxiv_id_handles_version_in_pdf():
    assert arxiv.extract_arxiv_id("https://arxiv.org/pdf/2310.06770v3.pdf") == "2310.06770"


def test_extract_arxiv_id_raises_on_non_paper_path():
    with pytest.raises(ValueError):
        arxiv.extract_arxiv_id("https://arxiv.org/abs/not-an-id")


# -------- _llamaparse_to_markdown --------


def _mock_client(post_response=None, get_responses=None):
    """Build a MagicMock httpx.Client that returns the given responses
    from .post and .get (cycling through get_responses)."""
    client = MagicMock(spec=httpx.Client)
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    if post_response is not None:
        client.post.return_value = post_response
    if get_responses is not None:
        client.get.side_effect = get_responses
    return client


def test_llamaparse_raises_when_api_key_missing():
    with pytest.raises(ValueError, match="api_key is unset"):
        arxiv._llamaparse_to_markdown(
            "https://arxiv.org/pdf/2310.06770.pdf",
            api_key="",
            base_url="https://api.cloud.eu.llamaindex.ai",
            tier="agentic_plus",
        )


def test_llamaparse_raises_on_submit_http_error():
    post_resp = MagicMock(status_code=500, text="server error")
    client = _mock_client(post_response=post_resp)
    with patch(
        "orchestrators.defs.extract_complex_contents.fetchers.arxiv.httpx.Client",
        return_value=client,
    ):
        with pytest.raises(ValueError, match="LlamaParse submit HTTP 500"):
            arxiv._llamaparse_to_markdown(
                "https://arxiv.org/pdf/2310.06770.pdf",
                api_key="k",
                base_url="https://api.cloud.eu.llamaindex.ai",
                tier="agentic_plus",
            )


def test_llamaparse_raises_when_submit_returns_no_job_id():
    post_resp = MagicMock(status_code=200)
    post_resp.json.return_value = {}
    client = _mock_client(post_response=post_resp)
    with patch(
        "orchestrators.defs.extract_complex_contents.fetchers.arxiv.httpx.Client",
        return_value=client,
    ):
        with pytest.raises(ValueError, match="no job id"):
            arxiv._llamaparse_to_markdown(
                "https://arxiv.org/pdf/2310.06770.pdf",
                api_key="k",
                base_url="https://api.cloud.eu.llamaindex.ai",
                tier="agentic_plus",
            )


def test_llamaparse_completes_on_first_poll():
    post_resp = MagicMock(status_code=200)
    post_resp.json.return_value = {"id": "job-1"}
    completed_resp = MagicMock(status_code=200)
    completed_resp.json.return_value = {
        "job": {"status": "COMPLETED"},
        "markdown_full": "# rendered paper body",
    }
    client = _mock_client(post_response=post_resp, get_responses=[completed_resp])
    with patch(
        "orchestrators.defs.extract_complex_contents.fetchers.arxiv.httpx.Client",
        return_value=client,
    ):
        md = arxiv._llamaparse_to_markdown(
            "https://arxiv.org/pdf/2310.06770.pdf",
            api_key="k",
            base_url="https://api.cloud.eu.llamaindex.ai",
            tier="agentic_plus",
        )
    assert md == "# rendered paper body"


def test_llamaparse_raises_on_failed_status():
    post_resp = MagicMock(status_code=200)
    post_resp.json.return_value = {"id": "job-2"}
    failed_resp = MagicMock(status_code=200)
    failed_resp.json.return_value = {
        "job": {"status": "FAILED", "error_message": "parse error"},
    }
    client = _mock_client(post_response=post_resp, get_responses=[failed_resp])
    with patch(
        "orchestrators.defs.extract_complex_contents.fetchers.arxiv.httpx.Client",
        return_value=client,
    ):
        with pytest.raises(ValueError, match="FAILED.*parse error"):
            arxiv._llamaparse_to_markdown(
                "https://arxiv.org/pdf/2310.06770.pdf",
                api_key="k",
                base_url="https://api.cloud.eu.llamaindex.ai",
                tier="agentic_plus",
            )


def test_llamaparse_raises_on_empty_markdown_completed():
    post_resp = MagicMock(status_code=200)
    post_resp.json.return_value = {"id": "job-3"}
    completed_resp = MagicMock(status_code=200)
    completed_resp.json.return_value = {
        "job": {"status": "COMPLETED"},
        "markdown_full": "",
    }
    client = _mock_client(post_response=post_resp, get_responses=[completed_resp])
    with patch(
        "orchestrators.defs.extract_complex_contents.fetchers.arxiv.httpx.Client",
        return_value=client,
    ):
        with pytest.raises(ValueError, match="completed with empty markdown"):
            arxiv._llamaparse_to_markdown(
                "https://arxiv.org/pdf/2310.06770.pdf",
                api_key="k",
                base_url="https://api.cloud.eu.llamaindex.ai",
                tier="agentic_plus",
            )
