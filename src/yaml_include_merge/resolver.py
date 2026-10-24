"""Typed, local-only include resolution helpers.

Resolvers locate a source but do not parse it. The loader validates the source
against configured roots, opens the returned stream, applies resource budgets,
and parses it with PyYAML's SafeLoader-based constructors.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable, Literal, Mapping, Protocol
from urllib.parse import urlsplit

from ._paths import resolve_include_file


@dataclass(frozen=True, slots=True)
class FileRoot:
    """A canonical filesystem directory allowed to contain includes."""

    path: Path


@dataclass(frozen=True, slots=True)
class MemoryRoot:
    """A virtual, in-memory include namespace."""

    namespace: str

    def __post_init__(self) -> None:
        if not self.namespace or "://" in self.namespace:
            raise ValueError("memory include namespaces must be non-empty local identifiers")


IncludeRoot = FileRoot | MemoryRoot


@dataclass(frozen=True, slots=True)
class FileSource:
    """A canonical filesystem source identity."""

    path: Path


@dataclass(frozen=True, slots=True)
class MemorySource:
    """A source identity inside one configured virtual namespace."""

    root: MemoryRoot
    path: PurePosixPath

    def __post_init__(self) -> None:
        if self.path.is_absolute() or not self.path.parts or ".." in self.path.parts:
            raise ValueError("memory source paths must stay within their namespace root")


IncludeSource = FileSource | MemorySource


@dataclass(frozen=True, slots=True)
class IncludeRequest:
    """Resolution context. Roots and search paths are already normalized."""

    name: str
    including: IncludeSource | None
    search_paths: tuple[IncludeRoot, ...]
    allowed_roots: tuple[IncludeRoot, ...]


@dataclass(frozen=True, slots=True)
class ResolvedInclude:
    """A source identity and a deferred opener.

    ``open_binary`` is called only after the loader has checked the returned
    identity against its allowed roots. Ownership of the opened stream passes
    to the loader, which closes it on success and every error path.
    """

    source: IncludeSource
    open_binary: Callable[[], BinaryIO]


class IncludeResolver(Protocol):
    """Resolve relative include names to local or in-memory byte streams.

    A resolver is trusted code: it must not perform network access and must
    treat the request's roots as an authorization boundary. The loader still
    checks returned identities and enforces parsing and byte/depth/count limits.
    """

    def resolve(self, request: IncludeRequest) -> ResolvedInclude:
        """Resolve a local include without parsing its YAML content."""


def validate_include_name(name: str) -> None:
    """Reject absolute, URI, and drive-qualified include names."""
    if not name or "\x00" in name or Path(name).is_absolute():
        raise ValueError("include paths must be non-empty relative paths")
    if urlsplit(name).scheme or re.match(r"^[A-Za-z]:", name):
        raise ValueError("include paths must be local relative paths, not URI or drive paths")


class FileSystemIncludeResolver:
    """The default resolver, limited to paths inside the request's file roots."""

    def resolve(self, request: IncludeRequest) -> ResolvedInclude:
        validate_include_name(request.name)
        if isinstance(request.including, MemorySource):
            raise ValueError("filesystem resolver cannot resolve relative to a memory source")
        roots = tuple(root.path for root in request.allowed_roots if isinstance(root, FileRoot))
        search_paths = tuple(root.path for root in request.search_paths if isinstance(root, FileRoot))
        if not roots:
            raise ValueError("filesystem include loading requires an allowed filesystem root")
        if any(not isinstance(root, FileRoot) for root in request.allowed_roots):
            raise ValueError("filesystem resolver cannot authorize a non-filesystem root")
        if any(not isinstance(root, FileRoot) for root in request.search_paths):
            raise ValueError("filesystem resolver cannot use a non-filesystem search path")

        current_file = request.including.path if isinstance(request.including, FileSource) else None
        path = resolve_include_file(request.name, current_file, search_paths, roots)
        return ResolvedInclude(FileSource(path), lambda: path.open("rb"))


class MemoryIncludeResolver:
    """Resolve files from a mapping for tests and explicitly in-memory configs.

    Keys are POSIX-style paths relative to ``root``. String values are encoded
    as UTF-8. No filesystem or network access is performed.
    """

    def __init__(self, root: MemoryRoot, files: Mapping[str, bytes | str]) -> None:
        self.root = root
        normalized: dict[str, bytes] = {}
        for name, content in files.items():
            key = self._normalize_path(PurePosixPath(name))
            normalized[key.as_posix()] = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        self._files = normalized

    @staticmethod
    def _normalize_path(path: PurePosixPath) -> PurePosixPath:
        normalized = PurePosixPath(posixpath.normpath(path.as_posix()))
        if normalized.is_absolute() or normalized == PurePosixPath(".") or ".." in normalized.parts:
            raise ValueError("memory include path escapes its namespace root")
        return normalized

    def resolve(self, request: IncludeRequest) -> ResolvedInclude:
        validate_include_name(request.name)
        if self.root not in request.allowed_roots:
            raise ValueError(f"memory root {self.root.namespace!r} is not allowed")
        if any(root != self.root for root in request.allowed_roots):
            raise ValueError("memory resolver cannot authorize a non-memory root")
        if any(root != self.root for root in request.search_paths):
            raise ValueError("memory resolver cannot use a different memory search path")

        requested = PurePosixPath(request.name)
        candidates: list[PurePosixPath] = []
        if isinstance(request.including, MemorySource):
            if request.including.root != self.root:
                raise ValueError("cannot resolve an include from a different memory root")
            candidates.append(request.including.path.parent / requested)
        elif isinstance(request.including, FileSource):
            raise ValueError("memory resolver cannot resolve relative to a filesystem source")
        candidates.append(requested)

        key: str | None = None
        for candidate in candidates:
            normalized = self._normalize_path(candidate)
            if normalized.as_posix() in self._files:
                key = normalized.as_posix()
                break
        if key is None:
            raise FileNotFoundError(f"memory include file {request.name!r} was not found")

        content = self._files[key]
        source = MemorySource(self.root, PurePosixPath(key))
        return ResolvedInclude(source, lambda: BytesIO(content))
