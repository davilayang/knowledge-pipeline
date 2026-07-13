"""Tests for the canonical arXiv URL identity shared across kp."""

import pytest
from domains.arxiv_urls import ARXIV_HOSTS, extract_arxiv_id, is_arxiv_id, is_arxiv_url


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://arxiv.org/abs/2401.00001", "2401.00001"),
        (
            "https://arxiv.org/pdf/2401.00001v2.pdf",
            "2401.00001",
        ),  # prefix + .pdf + version stripped
        ("https://arxiv.org/html/2606.09498v1", "2606.09498"),
        ("https://export.arxiv.org/abs/hep-th/9901001", "hep-th/9901001"),  # old-format ID
        ("https://example.com/abs/2401.00001", None),  # non-arxiv host
        ("https://arxiv.org/abs/not-an-id", None),  # arxiv host, unrecognisable path
    ],
)
def test_extract_arxiv_id(url: str, expected: str | None) -> None:
    assert extract_arxiv_id(url) == expected


def test_is_arxiv_url() -> None:
    assert is_arxiv_url("https://arxiv.org/abs/2401.00001") is True
    assert is_arxiv_url("https://example.com/paper") is False


def test_is_arxiv_id() -> None:
    assert is_arxiv_id("2401.00001") is True
    assert is_arxiv_id("hep-th/9901001") is True
    assert is_arxiv_id("garbage") is False


def test_arxiv_hosts_are_the_known_set() -> None:
    assert set(ARXIV_HOSTS) == {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
