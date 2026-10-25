"""Build and validate distributions using the same release pattern as mdrepo."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path


class ReleaseError(RuntimeError):
    """Raised when a release tag, build, or distribution validation fails."""


def project_version() -> str:
    """Read the SCM-resolved version from the installed project metadata."""
    try:
        return package_version("yaml-compose")
    except PackageNotFoundError as error:
        raise ReleaseError("install the project before building its distributions") from error


def verify_tag(tag: str | None = None) -> None:
    """Require the supplied tag or RELEASE_TAG to match the installed version."""
    release_tag = tag or os.environ.get("RELEASE_TAG")
    if not release_tag:
        raise ReleaseError("RELEASE_TAG is required for a release build")
    version = project_version()
    if release_tag.removeprefix("v") != version:
        raise ReleaseError(
            f"release tag {release_tag!r} does not match project version {version!r}"
        )


def build_distributions(*, root: Path, output: Path) -> tuple[Path, ...]:
    """Build a fresh wheel/sdist pair and validate it with Twine.

    Only prior wheel and source-archive files are removed from ``output``;
    other files are retained. The installed SCM version is passed to the
    backend, following mdrepo's build pattern. Reinstall the tagged checkout
    before building a release so that metadata describes that checkout.
    """
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for artifact in (*output.glob("*.whl"), *output.glob("*.tar.gz")):
        if artifact.is_file() or artifact.is_symlink():
            artifact.unlink()
    environment = os.environ.copy()
    environment["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_YAML_COMPOSE"] = project_version()
    command = [sys.executable, "-m", "build", "--outdir", str(output)]
    result = subprocess.run(command, cwd=root, env=environment, check=False)
    if result.returncode:
        raise ReleaseError(f"distribution build failed with status {result.returncode}")
    wheels = tuple(
        path for path in output.glob("*.whl") if path.is_file() and not path.is_symlink()
    )
    sources = tuple(
        path for path in output.glob("*.tar.gz") if path.is_file() and not path.is_symlink()
    )
    if len(wheels) != 1 or len(sources) != 1:
        raise ReleaseError("build must produce one wheel and one source distribution")
    distributions = (*wheels, *sources)
    command = [sys.executable, "-m", "twine", "check", "--strict", *map(str, distributions)]
    result = subprocess.run(command, cwd=root, check=False)
    if result.returncode:
        raise ReleaseError(f"Twine validation failed with status {result.returncode}")
    return distributions


def build_release(*, output: Path, tag: str | None = None) -> tuple[Path, ...]:
    """Verify the release tag, then build and validate its distributions."""
    verify_tag(tag)
    return build_distributions(root=Path(__file__).resolve().parents[1], output=output)


def main(argv: Sequence[str] = ()) -> int:
    """Run the provider-neutral release build command."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Build and validate the release distributions.")
    build.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args(argv)
    try:
        build_release(output=args.output)
    except (OSError, ReleaseError) as error:
        print(f"release: error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
