"""Compatibility entry point for ``python -m scripts.ci``."""

from __future__ import annotations

from collections.abc import Sequence

from scripts.ci import main as run_ci


def main(argv: Sequence[str] | None = None) -> int:
    """Forward arguments to the shared CI command coordinator."""
    return run_ci(argv)


if __name__ == "__main__":
    raise SystemExit(main())
