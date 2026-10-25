"""Release artifacts must retain licensing, typing, and their tagged version."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from tarfile import TarInfo
from tarfile import open as open_tar
from typing import Any, cast
from zipfile import ZipFile

import pytest
import yaml

from scripts.check_artifacts import main


@pytest.fixture
def distribution_files() -> tuple[dict[str, bytes], dict[str, bytes]]:
    license_content = (Path(__file__).resolve().parents[1] / "LICENSE").read_bytes()
    metadata = (
        b"Metadata-Version: 2.4\nName: yaml-compose\nVersion: 0.1.0\n"
        b"License-Expression: MIT\nLicense-File: LICENSE\nMaintainer: sheepfling\n\n"
    )
    return (
        {
            "yaml_compose/limits.py": b"",
            "yaml_compose/py.typed": b"",
            "yaml_compose-0.1.0.dist-info/METADATA": metadata,
            "yaml_compose-0.1.0.dist-info/licenses/LICENSE": license_content,
        },
        {
            "yaml_compose-0.1.0/PKG-INFO": metadata,
            "yaml_compose-0.1.0/LICENSE": license_content,
            "yaml_compose-0.1.0/src/yaml_compose/py.typed": b"",
        },
    )


def write_archives(
    directory: Path, wheel_files: dict[str, bytes], source_files: dict[str, bytes]
) -> None:
    with ZipFile(directory / "yaml_compose-0.1.0-py3-none-any.whl", "w") as wheel:
        for name, content in wheel_files.items():
            wheel.writestr(name, content)
    with open_tar(directory / "yaml_compose-0.1.0.tar.gz", "w:gz") as archive:
        for name, content in source_files.items():
            member = TarInfo(name)
            member.size = len(content)
            archive.addfile(member, BytesIO(content))


def test_release_artifacts_accept_matching_version_license_and_type_marker(
    test_path: Path, distribution_files: tuple[dict[str, bytes], dict[str, bytes]]
) -> None:
    write_archives(test_path, *distribution_files)
    assert main(["--dist-dir", str(test_path), "--release-tag", "v0.1.0"]) == 0


@pytest.mark.parametrize(
    ("target", "filename", "replacement", "message"),
    [
        (0, "yaml_compose/py.typed", None, "missing the py.typed marker"),
        (0, "yaml_compose-0.1.0.dist-info/licenses/LICENSE", None, "complete project LICENSE"),
        (
            0,
            "yaml_compose-0.1.0.dist-info/licenses/LICENSE",
            b"truncated",
            "complete project LICENSE",
        ),
        (1, "yaml_compose-0.1.0/LICENSE", None, "must contain LICENSE"),
        (1, "yaml_compose-0.1.0/LICENSE", b"truncated", "complete project LICENSE"),
        (1, "yaml_compose-0.1.0/src/yaml_compose/py.typed", None, "must contain LICENSE"),
    ],
)
def test_release_artifacts_reject_missing_or_damaged_release_files(
    test_path: Path,
    distribution_files: tuple[dict[str, bytes], dict[str, bytes]],
    target: int,
    filename: str,
    replacement: bytes | None,
    message: str,
) -> None:
    files = distribution_files[target]
    if replacement is None:
        del files[filename]
    else:
        files[filename] = replacement
    write_archives(test_path, *distribution_files)
    with pytest.raises(SystemExit, match=message):
        main(["--dist-dir", str(test_path)])


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (b"License-Expression: MIT", b"License-Expression: Apache-2.0", "License-Expression: MIT"),
        (b"License-File: LICENSE\n", b"", "License-File: LICENSE"),
        (b"Maintainer: sheepfling", b"Maintainer: a-person", "maintainer handle sheepfling"),
        (b"Maintainer: sheepfling", b"Author: a-person\nMaintainer: sheepfling", "author names"),
        (
            b"Maintainer: sheepfling",
            b"Maintainer-email: person@example.invalid\nMaintainer: sheepfling",
            "must not expose an author or maintainer email",
        ),
        (b"Version: 0.1.0", b"Version: 0.1.dev1", "does not match 0.1.0"),
    ],
)
def test_release_artifacts_reject_incorrect_metadata_or_personal_attribution(
    test_path: Path,
    distribution_files: tuple[dict[str, bytes], dict[str, bytes]],
    old: bytes,
    new: bytes,
    message: str,
) -> None:
    wheel_files, source_files = distribution_files
    name = "yaml_compose-0.1.0.dist-info/METADATA"
    wheel_files[name] = wheel_files[name].replace(old, new)
    write_archives(test_path, wheel_files, source_files)
    with pytest.raises(SystemExit, match=message):
        main(["--dist-dir", str(test_path), "--release-tag", "v0.1.0"])


def test_wheel_and_source_versions_must_agree(
    test_path: Path, distribution_files: tuple[dict[str, bytes], dict[str, bytes]]
) -> None:
    wheel_files, source_files = distribution_files
    name = "yaml_compose-0.1.0/PKG-INFO"
    source_files[name] = source_files[name].replace(b"Version: 0.1.0", b"Version: 0.2.0")
    write_archives(test_path, wheel_files, source_files)
    with pytest.raises(SystemExit, match=r"version 0\.2\.0 does not match 0\.1\.0"):
        main(["--dist-dir", str(test_path)])


@pytest.mark.parametrize("tag", ["main", "0.1.0", "v0.1", "v0.1.0rc1", "v0.1.0; echo invalid"])
def test_release_tag_requires_a_stable_version(test_path: Path, tag: str) -> None:
    with pytest.raises(SystemExit, match="release tag must have the form"):
        main(["--dist-dir", str(test_path), "--release-tag", tag])


def test_publishing_requires_the_matrix_build_validation_and_a_separate_identity_job() -> None:
    root = Path(__file__).resolve().parents[1]
    release = cast(
        dict[str, Any],
        yaml.load((root / ".github/workflows/release.yml").read_text(), Loader=yaml.BaseLoader),
    )
    checks = cast(
        dict[str, Any],
        yaml.load((root / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader),
    )
    assert release["on"] == {"release": {"types": ["published"]}}
    assert "workflow_call" in checks["on"]
    jobs = release["jobs"]
    assert jobs["checks"]["uses"] == "./.github/workflows/ci.yml"
    assert jobs["build"]["needs"] == "checks"
    assert jobs["publish"]["needs"] == "build"
    assert release["permissions"] == {"contents": "read"}
    assert jobs["publish"]["permissions"] == {"id-token": "write"}
    assert jobs["publish"]["environment"]["name"] == "pypi"
    steps = jobs["publish"]["steps"]
    assert [step["uses"] for step in steps] == [
        "actions/download-artifact@v4",
        "pypa/gh-action-pypi-publish@release/v1",
    ]
    assert any("--release-tag" in step.get("run", "") for step in jobs["build"]["steps"])
