"""Keep hosted CI and its local coordinator aligned with supported interpreters."""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from scripts import check_pyright, ci


def test_hosted_matrix_covers_declared_versions_and_runs_python_checks() -> None:
    root = Path(__file__).resolve().parents[1]
    metadata = (root / "pyproject.toml").read_text(encoding="utf-8")
    versions = re.findall(r'"Programming Language :: Python :: (3\.\d+)"', metadata)
    workflow = cast(dict[str, Any], yaml.safe_load((root / ".github/workflows/ci.yml").read_text()))
    job = workflow["jobs"]["test"]
    assert job["strategy"]["matrix"]["python-version"] == versions
    assert set(job["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "windows-latest"}
    assert job["strategy"]["fail-fast"] is False
    commands = [step["run"] for step in job["steps"] if "run" in step]
    assert 'python -m pip install -e ".[test,lint]"' in commands
    assert "python -m scripts.ci --python-only" in commands
    quality_commands = [
        step["run"] for step in workflow["jobs"]["quality"]["steps"] if "run" in step
    ]
    assert 'python -m pip install -e ".[dev]"' in quality_commands
    assert "python -m scripts.ci --quality-only" in quality_commands


@pytest.mark.parametrize(
    ("arguments", "has_python_checks", "has_tests", "has_repository_checks"),
    [
        ([], True, True, True),
        (["--python-only"], True, True, False),
        (["--tests-only"], False, True, False),
        (["--quality-only"], True, False, True),
    ],
)
def test_coordinator_modes_run_full_suites_with_the_active_interpreter(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    has_python_checks: bool,
    has_tests: bool,
    has_repository_checks: bool,
) -> None:
    commands: list[tuple[str, ...]] = []
    root = Path(__file__).resolve().parents[1]

    def find_rumdl() -> str:
        assert has_repository_checks, "Python-only runs must not require Markdown tooling"
        return "rumdl"

    def run(
        command: Sequence[str], *, cwd: Path, env: dict[str, str], check: bool
    ) -> subprocess.CompletedProcess[str]:
        assert cwd == root
        assert Path(env["TEMP"]).is_relative_to(root / "build" / "local-checks")
        assert check is False
        commands.append(tuple(command))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("scripts.ci._rumdl_command", find_rumdl)
    monkeypatch.setattr("scripts.ci.subprocess.run", run)
    assert ci.main(arguments) == 0
    python_commands = [command for command in commands if command[0] == sys.executable]
    assert all(command[1] == "-m" for command in python_commands)
    modules = [command[2] for command in python_commands]
    assert ("compileall" in modules) is has_python_checks
    assert ("ruff" in modules) is has_python_checks
    assert ("scripts.check_pyright" in modules) is has_python_checks
    assert ("pytest" in modules) is has_tests
    assert ("mdrepo" in modules) is has_repository_checks
    assert ("scripts.check_build" in modules) is has_repository_checks
    if has_tests:
        pytest_command = next(command for command in python_commands if command[2] == "pytest")
        # No paths or selectors: newly discovered semantic tests join CI automatically.
        assert all(argument.startswith("--cov") for argument in pytest_command[3:])
        assert "--cov-fail-under=80" in pytest_command
    if has_repository_checks:
        assert any(command[0] == "rumdl" for command in commands)
        assert any(
            command[1:5] == ("-m", "pre_commit", "run", "--all-files") for command in commands
        )


def test_coordinator_stops_and_propagates_a_failed_check(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[Sequence[str]] = []

    def run(
        command: Sequence[str], *, cwd: Path, env: dict[str, str], check: bool
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 42)

    monkeypatch.setattr("scripts.ci.subprocess.run", run)
    assert ci.main(["--python-only"]) == 42
    assert len(commands) == 1


def test_type_checks_target_the_active_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[Sequence[str]] = []

    def run(command: Sequence[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("scripts.check_pyright.subprocess.run", run)
    assert check_pyright.main() == 0
    assert commands == [
        [
            sys.executable,
            "-m",
            "pyright",
            "--pythonpath",
            sys.executable,
            "--pythonversion",
            f"{sys.version_info.major}.{sys.version_info.minor}",
        ]
    ]
