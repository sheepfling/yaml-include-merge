"""Bounded filesystem glob discovery confined before directory traversal."""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from ._search import is_explicit_relative, search_locations
from .resolver import FileRoot, FileSource, IncludeRequest, MemorySource


def _has_magic(part: str) -> bool:
    return any(character in part for character in "*?[")


def _confined(path: Path, roots: Iterable[Path]) -> Path:
    """Canonicalize before examining a directory or accepting a matched file."""
    resolved = path.resolve(strict=True)
    if not any(resolved.is_relative_to(root) for root in roots):
        raise ValueError(f"glob path {str(path)!r} resolves outside the allowed roots")
    return resolved


def _matches_at(
    base: Path,
    parts: Sequence[str],
    roots: Sequence[Path],
    step: Callable[[], None],
    max_matches: int | None,
) -> list[Path]:
    """Walk without following discovered directory symlinks.

    Parts need indexing; roots are traversed repeatedly for each candidate.
    Accept read-only sequences rather than requiring a concrete tuple.
    """
    step()
    try:
        base = _confined(base, roots)
    except FileNotFoundError:
        return []
    if not base.is_dir():
        return []
    pending = [(base, 0)]
    seen: set[tuple[Path, int]] = set()
    matches: set[Path] = set()

    def add(path: Path) -> None:
        # Only inspect target metadata here; never enumerate directory symlinks.
        if path.is_symlink() and path.is_dir():
            return
        try:
            resolved = _confined(path, roots)
        except FileNotFoundError:
            return
        if resolved.is_file():
            matches.add(path)
            if max_matches is not None and len(matches) > max_matches:
                raise ValueError("glob matches exceed the remaining maximum include count")

    while pending:
        directory, position = pending.pop()
        step()
        state = (directory, position)
        if state in seen:
            continue
        seen.add(state)
        directory = _confined(directory, roots)
        part = parts[position]
        last = position == len(parts) - 1
        recursive = part == "**"
        if recursive and not last:
            pending.append((directory, position + 1))
        names: list[str] = []
        with os.scandir(directory) as entries:
            for entry in entries:
                step()
                names.append(entry.name)
        for name in sorted(names):
            if not recursive and not fnmatch.fnmatch(name, part):
                continue
            child = directory / name
            if last:
                add(child)
            if not last or recursive:
                # A fixed, explicitly named prefix was already canonicalized.
                # Discovered symlink directories are never followed, even inside roots.
                if child.is_symlink():
                    continue
                try:
                    resolved = _confined(child, roots)
                except FileNotFoundError:
                    continue
                if resolved.is_dir():
                    pending.append((resolved, position if recursive else position + 1))
    return sorted(matches, key=lambda path: path.as_posix())


def expand_include_glob(
    request: IncludeRequest,
    *,
    step: Callable[[], None],
    max_matches: int | None,
) -> list[Path]:
    """Return files from the first matching lookup location in stable spelling order."""
    if isinstance(request.including, MemorySource):
        raise ValueError("filesystem globs cannot resolve relative to a memory source")
    if any(not isinstance(root, FileRoot) for root in (*request.allowed_roots, *request.search_paths)):
        raise ValueError("filesystem globs require filesystem search paths and allowed roots")
    roots = tuple(root.path for root in request.allowed_roots if isinstance(root, FileRoot))
    search = tuple(root.path for root in request.search_paths if isinstance(root, FileRoot))
    if not roots:
        raise ValueError("filesystem globs require at least one allowed root")
    requested = Path(request.name)
    parts = requested.parts
    if not parts or not requested.name:
        raise ValueError("glob must name a file pattern")
    first_component = 1 if requested.anchor else 0
    first_magic = next(
        (index for index in range(first_component, len(parts)) if _has_magic(parts[index])), len(parts) - 1
    )
    if ".." in parts[first_magic:]:
        raise ValueError("parent traversal after a glob wildcard is not supported")
    prefix = Path(*parts[:first_magic])
    remaining = parts[first_magic:]
    if requested.is_absolute():
        locations = (prefix,)
    else:
        local = request.including.path.parent if isinstance(request.including, FileSource) else None
        if is_explicit_relative(request.name):
            if local is None:
                raise ValueError("explicit relative globs require an including source")
            bases = (local,)
        else:
            bases = search_locations(local, search, request.include_search)
        locations = tuple(base / prefix for base in bases)
    for location in locations:
        matches = _matches_at(location, remaining, roots, step, max_matches)
        if matches:
            return matches
    raise FileNotFoundError(f"include glob {request.name!r} matched no files")
