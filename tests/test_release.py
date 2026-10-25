"""Release orchestration checks shared with the mdrepo build pattern."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts import release


def test_release_tag_must_match_the_installed_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "project_version", lambda: "1.2.3")
    monkeypatch.delenv("RELEASE_TAG", raising=False)
    with pytest.raises(release.ReleaseError, match="RELEASE_TAG is required"):
        release.verify_tag()
    with pytest.raises(release.ReleaseError, match="does not match"):
        release.verify_tag("v2.0.0")
    release.verify_tag("v1.2.3")
    monkeypatch.setenv("RELEASE_TAG", "v1.2.3")
    release.verify_tag()


def test_release_build_removes_stale_artifacts_and_validates_only_the_new_pair(
    test_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release, "project_version", lambda: "1.2.3")
    stale = test_path / "old-0.9.0-py3-none-any.whl"
    stale.touch()
    notes = test_path / "notes.txt"
    notes.write_text("retain this", encoding="utf-8")
    wheel = test_path / "yaml_compose-1.2.3-py3-none-any.whl"
    source = test_path / "yaml_compose-1.2.3.tar.gz"
    validated: list[str] = []

    def run(
        command: Sequence[str], *, cwd: Path, env: dict[str, str] | None = None, check: bool
    ) -> subprocess.CompletedProcess[str]:
        assert command[0] == sys.executable
        if command[2] == "build":
            assert env is not None
            assert env["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_YAML_COMPOSE"] == "1.2.3"
            assert not stale.exists()
            wheel.touch()
            source.touch()
        else:
            assert command[2:5] == ["twine", "check", "--strict"]
            validated.extend(command[5:])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(release.subprocess, "run", run)
    assert release.build_release(output=test_path, tag="v1.2.3") == (wheel, source)
    assert validated == [str(wheel), str(source)]
    assert notes.read_text(encoding="utf-8") == "retain this"


@pytest.mark.parametrize("failed_module", ["build", "twine"])
def test_failed_build_or_validation_is_reported(
    test_path: Path, monkeypatch: pytest.MonkeyPatch, failed_module: str
) -> None:
    monkeypatch.setattr(release, "project_version", lambda: "1.2.3")

    def run(
        command: Sequence[str], *, cwd: Path, env: dict[str, str] | None = None, check: bool
    ) -> subprocess.CompletedProcess[str]:
        if command[2] == "build":
            (test_path / "package.whl").touch()
            (test_path / "package.tar.gz").touch()
        return subprocess.CompletedProcess(command, 1 if command[2] == failed_module else 0)

    monkeypatch.setattr(release.subprocess, "run", run)
    with pytest.raises(release.ReleaseError, match="failed with status 1"):
        release.build_release(output=test_path, tag="v1.2.3")


def test_wrong_tag_does_not_remove_existing_output(
    test_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release, "project_version", lambda: "1.2.3")
    wheel = test_path / "keep.whl"
    wheel.touch()
    with pytest.raises(release.ReleaseError, match="does not match"):
        release.build_release(output=test_path, tag="v2.0.0")
    assert wheel.exists()
