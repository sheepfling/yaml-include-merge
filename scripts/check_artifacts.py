"""Check the wheel contents and keep repository examples out of the source archive."""

from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path, PurePosixPath
from tarfile import open as open_tar
from zipfile import ZipFile


def main() -> int:
    """Fail if build output is ambiguous or examples enter a package artifact."""
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    dist_dir = parser.parse_args().dist_dir

    wheels = tuple(dist_dir.glob("yaml_compose-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected one yaml-compose wheel in {dist_dir}, found {len(wheels)}")

    with ZipFile(wheels[0]) as wheel:
        members = set(wheel.namelist())
    if "yaml_compose/limits.py" not in members:
        raise SystemExit("wheel is missing the public include limits module")

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
    examples = sorted(name for name in source_members if "examples" in PurePosixPath(name).parts)
    if examples:
        raise SystemExit(f"source archive contains repository examples: {examples}")

    print(f"Checked {wheels[0].name}: no example or test files")
    print(f"Checked {sdists[0].name}: no examples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
