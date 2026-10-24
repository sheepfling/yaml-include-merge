from __future__ import annotations

from pathlib import Path
from typing import Iterable


def resolve_include_file(
    name: str,
    current_file: Path | None,
    search_paths: Iterable[Path],
    allowed_roots: Iterable[Path],
) -> Path:
    """Resolve an include relative to its parent file or configured paths.

    Every candidate is canonicalized before the root check, so a symlink that
    points outside an allowed root is rejected as well.
    """
    requested = Path(name)
    if requested.is_absolute():
        raise ValueError("include paths must be relative")

    roots = tuple(Path(root).resolve(strict=True) for root in allowed_roots)
    if not roots:
        raise ValueError("include loading requires at least one allowed root")

    candidates: list[Path] = []
    if current_file is not None:
        candidates.append(current_file.parent / requested)
    candidates.extend(Path(root) / requested for root in search_paths)

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
