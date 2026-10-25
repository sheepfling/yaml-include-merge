"""Shared pytest fixtures for isolated filesystem tests."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def test_path() -> Iterator[Path]:
    """Create a writable directory without depending on the system temp root."""
    checkout = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="yaml-compose-test-", dir=checkout) as directory:
        yield Path(directory).resolve()
