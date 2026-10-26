"""Typed, local-only include resolution helpers.

Resolvers locate a source but do not parse it. The loader validates the source
against configured roots, opens the returned stream, applies resource budgets,
and parses it with the selected PyYAML loader (SafeLoader by default).
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol
from urllib.parse import urlsplit

from ._paths import resolve_include_file
from ._search import IncludeSearch, is_explicit_relative, search_locations, validate_include_search


@dataclass(frozen=True, slots=True)
class FileRoot:
    """A filesystem directory that may contain included files.

    ``path`` is resolved to a canonical directory by the loader before it is
    used in an include request.
    """

    path: Path


@dataclass(frozen=True, slots=True)
class MemoryRoot:
    """A named virtual root for in-memory includes.

    ``namespace`` must be a non-empty local identifier without a URI scheme.
    Invalid namespaces raise ``ValueError``.
    """

    namespace: str

    def __post_init__(self) -> None:
        """Validate the virtual namespace before it can authorize includes."""
        if not self.namespace or urlsplit(self.namespace).scheme:
            raise ValueError("memory include namespaces must be non-empty local identifiers")


#: One configured root, either a filesystem directory or a virtual namespace.
IncludeRoot = FileRoot | MemoryRoot


@dataclass(frozen=True, slots=True)
class FileSource:
    """Identity of one filesystem YAML source.

    ``path`` is resolved by the loader before root and cycle checks.
    """

    path: Path


@dataclass(frozen=True, slots=True)
class MemorySource:
    """Identity of one file inside a ``MemoryRoot``.

    ``path`` is a relative POSIX path within ``root``. Absolute paths, empty
    paths, and parent traversal raise ``ValueError``.
    """

    root: MemoryRoot
    path: PurePosixPath

    def __post_init__(self) -> None:
        """Keep the source path inside its virtual root."""
        if self.path.is_absolute() or not self.path.parts or ".." in self.path.parts:
            raise ValueError("memory source paths must stay within their namespace root")


#: The identity of a filesystem or in-memory YAML document.
IncludeSource = FileSource | MemorySource


@dataclass(frozen=True, slots=True)
class IncludeRequest:
    """Context passed to an ``IncludeResolver`` for one ``!include`` read.

    ``name`` is the requested include path. ``including`` is the current source
    or ``None`` for a source-free stream. ``search_paths`` control lookup order;
    ``allowed_roots`` are the authorization boundary. The loader has already
    normalized both root tuples before invoking a resolver.
    ``include_search`` selects local-first, search-first, local-only, or
    search-only lookup for bare names. Explicit relative paths stay anchored
    to ``including``. Custom resolvers should honor that policy.
    """

    name: str
    including: IncludeSource | None
    search_paths: tuple[IncludeRoot, ...]
    allowed_roots: tuple[IncludeRoot, ...]
    include_search: IncludeSearch = "local-first"

    def __post_init__(self) -> None:
        """Reject unsupported lookup policies at the request boundary."""
        validate_include_search(self.include_search)


@dataclass(frozen=True, slots=True)
class ResolvedInclude:
    """A source identity and a deferred opener.

    ``source`` must identify a file within the request's allowed roots.
    ``open_binary`` is called only after the loader checks that identity. It
    must return a readable binary stream. Ownership passes to the loader, which
    closes it on success and every error path.
    """

    source: IncludeSource
    open_binary: Callable[[], BinaryIO]


class IncludeResolver(Protocol):
    """Resolve include names to local or in-memory byte streams.

    A resolver is trusted code: it must not perform network access and must
    treat the request's roots as an authorization boundary. The loader checks
    returned identities, closes opened streams, and enforces parse budgets.
    """

    def resolve(self, request: IncludeRequest) -> ResolvedInclude:
        """Return a source identity and a deferred binary opener.

        Raise ``OSError`` or ``ValueError`` if the name cannot be resolved.
        The loader converts those failures into marked YAML include errors.
        """
        ...


def validate_include_name(name: str, *, allow_absolute: bool = False) -> None:
    """Validate a local include path before looking it up.

    URI schemes, NUL bytes, and drive-relative paths raise ``ValueError``.
    Full absolute filesystem paths are accepted only with ``allow_absolute``;
    the resolver must still keep them inside an allowed root.
    """
    if not name or "\x00" in name:
        raise ValueError("include paths must be non-empty local paths")
    requested = Path(name)
    if requested.drive and not requested.is_absolute():
        raise ValueError("include paths must be local paths, not URI or drive-relative paths")
    if urlsplit(name).scheme and not (requested.drive and requested.is_absolute()):
        raise ValueError("include paths must be local paths, not URI or drive paths")
    if requested.anchor and not requested.is_absolute():
        raise ValueError("include paths must be relative or fully qualified absolute paths")
    if requested.is_absolute() and not allow_absolute:
        raise ValueError("include paths must be relative paths")


class FileSystemIncludeResolver(IncludeResolver):
    """Resolve local files within the request's allowed filesystem roots.

    The candidate is canonicalized before the root check, so symlinks cannot
    escape the allowed directories. File contents remain unopened until the
    loader validates the returned ``FileSource``.
    """

    def resolve(self, request: IncludeRequest) -> ResolvedInclude:
        """Find a file using the request's include search policy.

        Absolute local names are accepted when their resolved files stay under
        an allowed root. Missing files raise ``FileNotFoundError``; invalid
        roots and boundary violations raise ``ValueError``.
        """
        validate_include_name(request.name, allow_absolute=True)
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
        path = resolve_include_file(
            request.name, current_file, search_paths, roots, include_search=request.include_search
        )
        return ResolvedInclude(FileSource(path), lambda: path.open("rb"))


class MemoryIncludeResolver(IncludeResolver):
    """Resolve files from a mapping for tests and explicitly in-memory configs.

    Keys are POSIX-style paths relative to ``root``. String values are encoded
    as UTF-8; bytes are used directly. No filesystem or network access occurs.
    The namespace root is this resolver's search location. The request's
    policy orders or restricts lookup between that root and the including
    file's directory. Explicit relative names use only the latter.
    """

    def __init__(self, root: MemoryRoot, files: Mapping[str, bytes | str]) -> None:
        """Copy and normalize the virtual file mapping under ``root``.

        Invalid or escaping file names raise ``ValueError``. The resulting
        resolver owns its byte copies and is independent of later mapping edits.
        """
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
        """Return a virtual source and a deferred ``BytesIO`` opener.

        The request must authorize this exact memory root. A missing file
        raises ``FileNotFoundError``; mismatched roots raise ``ValueError``.
        """
        validate_include_name(request.name)
        if self.root not in request.allowed_roots:
            raise ValueError(f"memory root {self.root.namespace!r} is not allowed")
        if any(root != self.root for root in request.allowed_roots):
            raise ValueError("memory resolver cannot authorize a non-memory root")
        if any(root != self.root for root in request.search_paths):
            raise ValueError("memory resolver cannot use a different memory search path")

        requested = PurePosixPath(request.name)
        local: PurePosixPath | None = None
        if isinstance(request.including, MemorySource):
            if request.including.root != self.root:
                raise ValueError("cannot resolve an include from a different memory root")
            local = request.including.path.parent
        elif isinstance(request.including, FileSource):
            raise ValueError("memory resolver cannot resolve relative to a filesystem source")
        if is_explicit_relative(request.name):
            if local is None:
                raise ValueError("explicit relative includes require an including source")
            locations = (local,)
        else:
            locations = search_locations(local, (PurePosixPath("."),), request.include_search)
        candidates = (location / requested for location in locations)

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
