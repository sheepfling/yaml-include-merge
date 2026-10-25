"""Shared include lookup policies for filesystem and virtual sources."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePath
from typing import Literal, TypeVar

IncludeSearch = Literal["local-first", "search-first", "local-only", "search-only"]
"""Lookup policy for include names without an explicit relative prefix.

``local`` means the directory of the document containing that include.
``search`` means the configured search locations in their supplied order.
Absolute paths and explicit ``./`` or ``../`` paths bypass this policy.
"""

_TPath = TypeVar("_TPath", bound=PurePath)


def validate_include_search(value: object) -> None:
    """Reject unknown lookup policies before any document is loaded."""
    if not isinstance(value, str) or value not in (
        "local-first",
        "search-first",
        "local-only",
        "search-only",
    ):
        raise ValueError(
            "include_search must be local-first, search-first, local-only, or search-only"
        )


def is_explicit_relative(name: str) -> bool:
    """Recognize a containing-file reference before path normalization removes dots."""
    return name in (".", "..") or name.startswith(("./", "../", ".\\", "..\\"))


def search_locations(
    local: _TPath | None, search_paths: Iterable[_TPath], policy: IncludeSearch
) -> tuple[_TPath, ...]:
    """Order candidate directories without changing which roots are authorized."""
    validate_include_search(policy)
    nearby = () if local is None else (local,)
    configured = tuple(search_paths)
    if policy == "local-first":
        return (*nearby, *configured)
    if policy == "search-first":
        return (*configured, *nearby)
    if policy == "local-only":
        return nearby
    return configured
