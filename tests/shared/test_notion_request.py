"""Stub (design-time): the shared Notion request helper — throttle, 429 + 5xx/timeout retry."""

import pytest


def test_retries_429_honouring_retry_after():
    pytest.fail("stub")


def test_retries_5xx_and_timeouts_up_to_budget():
    pytest.fail("stub")


def test_raises_404_without_retry():
    pytest.fail("stub")
