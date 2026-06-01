"""Unit tests for the arXiv fetcher — URL classification and ID extraction.

Network calls (arxiv.Client, httpx PDF download) are integration-level
and not covered here. URL helpers are pure-Python and fully testable.
"""

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
