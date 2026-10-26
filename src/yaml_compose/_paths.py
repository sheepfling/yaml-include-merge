"""Canonical filesystem lookup for includes within explicitly allowed roots."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ._search import IncludeSearch, is_explicit_relative, search_locations


def resolve_include_file(
    name: str,
    current_file: Path | None,
    search_paths: Iterable[Path],
    allowed_roots: Iterable[Path],
    *,
    include_search: IncludeSearch = "local-first",
) -> Path:
    """Resolve a local include against its parent, search paths, or absolute path.

    Roots arrive canonicalized from the loader. Each candidate is canonicalized
    before the root check, so a symlink outside an allowed root is rejected.
    Bare names follow ``include_search``. Explicit ``./`` and ``../`` names
    require a containing file and never fall back to a search directory.
    Absolute paths name one exact file independently of the search policy.
    """
    requested = Path(name)

    roots = tuple(allowed_roots)
    if not roots:
        raise ValueError("include loading requires at least one allowed root")

    candidates: list[Path] = []
    if requested.is_absolute():
        candidates.append(requested)
    elif is_explicit_relative(name):
        if current_file is None:
            raise ValueError("explicit relative includes require an including source")
        candidates.append(current_file.parent / requested)
    else:
        local = current_file.parent if current_file is not None else None
        candidates.extend(root / requested for root in search_locations(local, search_paths, include_search))

    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError:
            continue
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"cannot safely resolve include path {name!r}: {exc}") from exc

        if not resolved.is_file():
            continue
        if not any(resolved.is_relative_to(root) for root in roots):
            raise ValueError(f"include path {name!r} resolves outside the allowed roots")
        return resolved

    raise FileNotFoundError(f"include file {name!r} was not found")
