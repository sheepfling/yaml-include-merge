"""Run strict Pyright against the active Python environment."""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    """Use the interpreter that installed this project's dependencies."""
    return subprocess.run(
        [sys.executable, "-m", "pyright", "--pythonpath", sys.executable],
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
