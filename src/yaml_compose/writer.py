"""Write a single YAML document with explicit yaml-compose includes."""

from dataclasses import dataclass
from os import PathLike
from pathlib import Path, PurePosixPath, PureWindowsPath

from yaml import SafeDumper, ScalarNode
from yaml import dump as _yaml_dump

from .tags import COMPOSE_LOADER_TAG, INCLUDE_TABLE_TAG, INCLUDE_TAG, LOADER_DIRECTIVE

__all__ = ("Include", "IncludeTable", "dump")


def _require_relative_path(path: str, marker: str) -> None:
    if not path or PurePosixPath(path).is_absolute() or PureWindowsPath(path).anchor:
        raise ValueError(f"{marker} requires a nonempty relative path")


@dataclass(frozen=True, slots=True)
class Include:
    """Mark a literal relative path to emit as a scalar `!include`."""

    path: str

    def __post_init__(self) -> None:
        """Reject empty paths, absolute paths, and Windows drive-relative paths."""
        _require_relative_path(self.path, "Include")


@dataclass(frozen=True, slots=True)
class IncludeTable:
    """Mark a literal relative path to emit as a scalar `!include-table`."""

    path: str

    def __post_init__(self) -> None:
        """Reject empty paths, absolute paths, and Windows drive-relative paths."""
        _require_relative_path(self.path, "IncludeTable")


class _ComposeDumper(SafeDumper):
    """Keep the include representer local to the compose writer."""


def _represent_include(_dumper: SafeDumper, value: Include) -> ScalarNode:
    return ScalarNode(INCLUDE_TAG, value.path)


def _represent_include_table(_dumper: SafeDumper, value: IncludeTable) -> ScalarNode:
    return ScalarNode(INCLUDE_TABLE_TAG, value.path)


_ComposeDumper.add_representer(Include, _represent_include)
_ComposeDumper.add_representer(IncludeTable, _represent_include_table)


def dump(document: object, path: str | PathLike[str]) -> None:
    """Write one UTF-8 compose document, preserving literal include references.

    Serialize before opening the destination so unsupported objects do not
    truncate an existing file. Data must be supported by PyYAML's safe dumper
    or use an explicit `Include` or `IncludeTable` marker.
    """
    body: str = _yaml_dump(document, Dumper=_ComposeDumper, sort_keys=False, allow_unicode=True)
    with Path(path).open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(f"%{LOADER_DIRECTIVE} {COMPOSE_LOADER_TAG}\n---\n{body}")
