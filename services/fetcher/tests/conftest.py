"""Shared pytest fixtures."""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> str:
    """Per-test SQLite DB path; auto-cleaned by tmp_path."""
    return str(tmp_path / "test_fetches.db")
