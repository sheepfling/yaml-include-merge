"""Check packaging with a temporary build and standard Twine validation."""

from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.release import ReleaseError, build_distributions


def main() -> int:
    """Build both distributions without leaving artifacts in the release directory."""
    root = Path(__file__).resolve().parents[1]
    temporary_root = root / "build" / "checks"
    temporary_root.mkdir(parents=True, exist_ok=True)
    try:
        with TemporaryDirectory(prefix="package-", dir=temporary_root) as temporary:
            build_distributions(root=root, output=Path(temporary) / "dist")
    except (OSError, ReleaseError) as error:
        print(f"build: error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
