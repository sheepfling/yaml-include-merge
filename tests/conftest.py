"""Shared pytest fixtures for isolated filesystem tests."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from scripts import repository_root


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Share the scripts' checkout root across the test session."""
    return repository_root()


@pytest.fixture(scope="session")
def examples_path(repo_root: Path) -> Path:
    """Locate the runnable examples relative to the configured checkout."""
    return repo_root / "examples"


@pytest.fixture
def test_path(repo_root: Path) -> Iterator[Path]:
    """Create a writable directory without depending on the system temp root."""
    with tempfile.TemporaryDirectory(prefix="yaml-compose-test-", dir=repo_root) as directory:
        yield Path(directory).resolve()
