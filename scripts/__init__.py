"""Development check helpers."""

from pathlib import Path


def repository_root() -> Path:
    """Return the checkout containing these scripts, independently of the caller's cwd."""
    return Path(__file__).resolve().parent.parent
