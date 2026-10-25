"""Validate package contents, public metadata, and optional release-tag agreement."""

from __future__ import annotations

from argparse import ArgumentParser
from collections.abc import Sequence
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from re import fullmatch
from tarfile import open as open_tar
from zipfile import ZipFile


def _metadata_version(content: bytes, expected_version: str | None) -> str:
    """Check the name, MIT declaration, attribution, and version of one distribution."""
    metadata = BytesParser().parsebytes(content)
    if metadata.get("Name") != "yaml-compose":
        raise SystemExit("distribution name must be yaml-compose")
    if metadata.get("License-Expression") != "MIT":
        raise SystemExit("distribution must declare License-Expression: MIT")
    if "LICENSE" not in metadata.get_all("License-File", []):
        raise SystemExit("distribution must declare License-File: LICENSE")
    if metadata.get("Maintainer") != "sheepfling":
        raise SystemExit("distribution must credit the maintainer handle sheepfling")
    if metadata.get("Author"):
        raise SystemExit("distribution must use the maintainer credit without author names")
    if metadata.get("Author-email") or metadata.get("Maintainer-email"):
        raise SystemExit("distribution must not expose an author or maintainer email")
    version = metadata.get("Version")
    if not isinstance(version, str) or not version:
        raise SystemExit("distribution is missing its version")
    if expected_version is not None and version != expected_version:
        raise SystemExit(f"distribution version {version} does not match {expected_version}")
    return version


def main(argv: Sequence[str] | None = None) -> int:
    """Validate a wheel/sdist pair, requiring a stable tagged version when requested."""
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    parser.add_argument("--release-tag", help="require an exact vMAJOR.MINOR.PATCH release tag")
    args = parser.parse_args(argv)
    dist_dir = args.dist_dir
    expected_version = None
    if args.release_tag is not None:
        match = fullmatch(r"v([0-9]+\.[0-9]+\.[0-9]+)", args.release_tag)
        if match is None:
            raise SystemExit("release tag must have the form vMAJOR.MINOR.PATCH")
        expected_version = match.group(1)
    license_content = (Path(__file__).resolve().parents[1] / "LICENSE").read_bytes()

    wheels = tuple(dist_dir.glob("yaml_compose-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected one yaml-compose wheel in {dist_dir}, found {len(wheels)}")

    with ZipFile(wheels[0]) as wheel:
        members = set(wheel.namelist())
        metadata_paths = [name for name in members if name.endswith(".dist-info/METADATA")]
        if len(metadata_paths) != 1:
            raise SystemExit("wheel must contain exactly one distribution metadata file")
        version = _metadata_version(wheel.read(metadata_paths[0]), expected_version)
        license_path = str(PurePosixPath(metadata_paths[0]).parent / "licenses" / "LICENSE")
        if license_path not in members or wheel.read(license_path) != license_content:
            raise SystemExit("wheel must bundle the complete project LICENSE")
    if "yaml_compose/limits.py" not in members:
        raise SystemExit("wheel is missing the public include limits module")
    if "yaml_compose/py.typed" not in members:
        raise SystemExit("wheel is missing the py.typed marker for its public type annotations")

    unexpected = sorted(
        name
        for name in members
        if PurePosixPath(name).parts[0] != "yaml_compose"
        and not PurePosixPath(name).parts[0].endswith(".dist-info")
    )
    if unexpected:
        raise SystemExit(f"wheel contains files outside yaml_compose and metadata: {unexpected}")

    unexpected = sorted(
        name
        for name in members
        if {"examples", "tests"}.intersection(PurePosixPath(name).parts)
        or PurePosixPath(name).suffix in {".yaml", ".yml"}
    )
    if unexpected:
        raise SystemExit(f"wheel contains example or test files: {unexpected}")

    sdists = tuple(dist_dir.glob("yaml_compose-*.tar.gz"))
    if len(sdists) != 1:
        raise SystemExit(
            f"expected one yaml-compose source archive in {dist_dir}, found {len(sdists)}"
        )

    with open_tar(sdists[0], "r:gz") as source_archive:
        source_members = source_archive.getnames()
        roots = {PurePosixPath(name).parts[0] for name in source_members}
        if len(roots) != 1:
            raise SystemExit("source archive must have one top-level directory")
        root = roots.pop()
        required = {
            f"{root}/LICENSE",
            f"{root}/PKG-INFO",
            f"{root}/src/yaml_compose/py.typed",
        }
        if not required.issubset(source_members):
            raise SystemExit("source archive must contain LICENSE, PKG-INFO, and py.typed")
        license_stream = source_archive.extractfile(f"{root}/LICENSE")
        metadata_stream = source_archive.extractfile(f"{root}/PKG-INFO")
        if license_stream is None or metadata_stream is None:
            raise SystemExit("source archive license and metadata must be regular files")
        with license_stream, metadata_stream:
            if license_stream.read() != license_content:
                raise SystemExit("source archive must bundle the complete project LICENSE")
            _metadata_version(metadata_stream.read(), version)
    examples = sorted(name for name in source_members if "examples" in PurePosixPath(name).parts)
    if examples:
        raise SystemExit(f"source archive contains repository examples: {examples}")

    print(f"Checked {wheels[0].name}: no example or test files")
    print(f"Checked {sdists[0].name}: no examples")
    print(f"Checked MIT license, public attribution, type marker, and version {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
