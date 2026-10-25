"""Run this repository's CI checks locally and in GitHub Actions."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
PYTHON_PATHS = ("src", "examples", "scripts", "tests")
Mode = Literal["all", "tests", "quality"]


def _rumdl_command() -> str:
    """Find rumdl in the active Python environment, including Windows Scripts."""
    environment_path = os.pathsep.join(
        (str(Path(sys.executable).parent), os.environ.get("PATH", ""))
    )
    executable = shutil.which("rumdl", path=environment_path)
    if executable is not None:
        return executable
    for name in ("rumdl.exe", "rumdl"):
        sibling = Path(sys.executable).with_name(name)
        if sibling.exists():
            return str(sibling)
    raise FileNotFoundError(
        "rumdl is not installed; install development dependencies with "
        '`python -m pip install -e ".[dev]"`'
    )


def _commands(
    python: str,
    rumdl: str,
    dist: Path,
    *,
    mode: Mode,
    fix: bool,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return the ordered CI commands selected for this run."""
    quality: list[tuple[str, tuple[str, ...]]] = []
    if fix:
        quality.extend(
            [
                ("Ruff fixes", (python, "-m", "ruff", "check", "--fix", *PYTHON_PATHS)),
                ("Ruff format", (python, "-m", "ruff", "format", *PYTHON_PATHS)),
                ("Markdown fixes", (rumdl, "check", "--fix", ".")),
                ("Markdown repository fixes", (python, "-m", "mdrepo", "fix", ".")),
            ]
        )
    quality.extend(
        [
            ("compile", (python, "-m", "compileall", "-q", *PYTHON_PATHS)),
            ("Ruff lint", (python, "-m", "ruff", "check", *PYTHON_PATHS)),
            (
                "Ruff format check",
                (python, "-m", "ruff", "format", "--check", *PYTHON_PATHS),
            ),
            ("Pyright", (python, "-m", "scripts.check_pyright")),
            ("rumdl", (rumdl, "check", ".")),
            ("mdrepo", (python, "-m", "mdrepo", "check", ".", "--format", "github")),
            (
                "pre-commit configuration",
                (python, "-m", "pre_commit", "validate-config", ".pre-commit-config.yaml"),
            ),
            ("pre-commit hooks", (python, "-m", "pre_commit", "run", "--all-files")),
            (
                "package build",
                (
                    python,
                    "-m",
                    "build",
                    "--no-isolation",
                    "--quiet",
                    "--sdist",
                    "--wheel",
                    "--outdir",
                    str(dist),
                ),
            ),
            (
                "package contents",
                (python, "-m", "scripts.check_artifacts", "--dist-dir", str(dist)),
            ),
            ("distribution metadata", (python, "-m", "twine", "check")),
        ]
    )
    tests = [
        (
            "pytest coverage",
            (
                python,
                "-m",
                "pytest",
                "--cov=yaml_compose",
                "--cov-report=term-missing",
                "--cov-fail-under=80",
            ),
        )
    ]

    if mode == "tests":
        return tuple(tests)
    if mode == "quality":
        return tuple(quality)
    return tuple((*quality[:4], *tests, *quality[4:]))


def _run(command: tuple[str, ...], environment: dict[str, str]) -> int:
    """Run one command from the repository root and return its exit code."""
    print("$", " ".join(command), flush=True)
    try:
        result = subprocess.run(command, cwd=ROOT, env=environment, check=False)
    except OSError as error:
        print(f"CI command could not start: {error}", file=sys.stderr)
        return 1
    if result.returncode:
        print(f"CI command failed with exit code {result.returncode}", file=sys.stderr)
    return result.returncode


def main(argv: Sequence[str] = ()) -> int:
    """Run all checks locally, or one check group as selected by hosted CI."""
    parser = argparse.ArgumentParser(
        description="Run the same checks locally and in GitHub Actions.",
        epilog=(
            "By default this runs tests and quality checks. --fix applies Ruff, rumdl, "
            "and mdrepo safe fixes before checking."
        ),
    )
    selected = parser.add_mutually_exclusive_group()
    selected.add_argument("--tests-only", action="store_true", help="run only tests and coverage")
    selected.add_argument("--quality-only", action="store_true", help="run only quality checks")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="apply safe Ruff, rumdl, and mdrepo fixes before quality checks",
    )
    args = parser.parse_args(argv)
    if args.fix and args.tests_only:
        parser.error("--fix cannot be combined with --tests-only")

    mode: Mode = "tests" if args.tests_only else "quality" if args.quality_only else "all"
    python = sys.executable
    command_path = os.pathsep.join((str(Path(python).parent), os.environ.get("PATH", "")))
    try:
        rumdl = _rumdl_command() if mode != "tests" else "rumdl"
    except FileNotFoundError as error:
        print(error, file=sys.stderr)
        return 1

    build_root = (ROOT / "build" / "local-checks").resolve()
    if not build_root.is_relative_to(ROOT):
        raise ValueError("local check output must stay inside the repository")
    build_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="run-", dir=build_root) as temporary:
        dist = Path(temporary) / "dist"
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": command_path,
                "PRE_COMMIT_HOME": str(ROOT / ".pre-commit-cache"),
                "TEMP": temporary,
                "TMP": temporary,
                "TMPDIR": temporary,
            }
        )
        commands = _commands(python, rumdl, dist, mode=mode, fix=args.fix)
        for name, command in commands:
            if name == "distribution metadata":
                command = (*command, *(str(path) for path in sorted(dist.iterdir())))
            result = _run(command, environment)
            if result:
                print(f"Failed check: {name}", file=sys.stderr)
                return result

    print("All CI checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
