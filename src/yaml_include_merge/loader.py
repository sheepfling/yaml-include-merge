from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Hashable, Iterable, TextIO

import yaml
from yaml import MappingNode, Node, SafeLoader, ScalarNode
from yaml.constructor import ConstructorError

from .resolver import (
    FileRoot,
    FileSource,
    FileSystemIncludeResolver,
    IncludeRequest,
    IncludeResolver,
    IncludeRoot,
    IncludeSource,
    MemoryRoot,
    MemorySource,
    validate_include_name,
)
from ._unset import UNSET, UnsetType

__all__ = (
    "IncludeLoader",
    "YamlConfigLoader",
    "YamlDirective",
    "YamlIncludeError",
    "load_yaml_dispatch",
    "load_yaml_file",
    "load_yaml_include",
)

log = logging.getLogger(__name__)
_directive_regex = re.compile(r"^%(?P<directive>[^ \t]+)(?:[ \t]+(?P<value>.*))?$")
_merge_tag = "tag:yaml.org,2002:merge"
_merge_tag_value = "<<"
_DELETE = object()

YamlDict = dict[Hashable, Any]
YamlType = YamlDict | list[Any] | str | int | float | bool | None


class YamlIncludeError(ValueError):
    """Raised when an include or configuration overlay violates its policy."""


@dataclass(frozen=True, slots=True)
class YamlDirective:
    directive: str
    value: str


@dataclass(slots=True)
class _IncludeBudget:
    max_include_depth: int
    max_include_files: int
    max_file_bytes: int
    max_total_include_bytes: int
    include_files: int = 0
    include_bytes: int = 0
    current_depth: int = 0


class _NamedStringIO(StringIO):
    def __init__(self, text: str, name: str) -> None:
        super().__init__(text)
        self.name = name


def _stream_name(stream: TextIO) -> str:
    name = getattr(stream, "name", None)
    if isinstance(name, (str, Path)):
        value = str(name)
        if value and not (value.startswith("<") and value.endswith(">")):
            return value
    return "<yaml stream>"


def _source_path(stream: TextIO, source_path: str | Path | None) -> Path | None:
    candidate = source_path if source_path is not None else _stream_name(stream)
    if candidate == "<yaml stream>":
        return None
    return Path(candidate).resolve(strict=True)


def _source_ref(
    stream: TextIO,
    source_path: str | Path | None,
    source: IncludeSource | None,
) -> IncludeSource | None:
    if source is not None and source_path is not None:
        raise ValueError("pass either source or source_path, not both")
    if source is not None:
        if isinstance(source, FileSource):
            return FileSource(Path(source.path).resolve(strict=True))
        if isinstance(source, MemorySource):
            return source
        raise TypeError("source must be a FileSource or MemorySource")
    path = _source_path(stream, source_path)
    return FileSource(path) if path is not None else None


def _header_directives(text: str) -> tuple[list[YamlDirective], list[int]]:
    directives: list[YamlDirective] = []
    directive_lines: list[int] = []
    in_header = True
    document_started = False
    for index, line in enumerate(text.splitlines(keepends=True)):
        content = line.rstrip("\r\n")
        if not in_header:
            break
        if not content.strip() or content.lstrip().startswith("#"):
            continue
        if re.fullmatch(r"---(?:[ \t].*)?", content.strip()):
            document_started = True
            break
        match = _directive_regex.match(content)
        if match is not None:
            directives.append(
                YamlDirective(
                    directive=match.group("directive"),
                    value=match.group("value") or "",
                )
            )
            if match.group("directive") == "LOADER":
                directive_lines.append(index)
            continue
        in_header = False
    if directive_lines and not document_started:
        raise YamlIncludeError("%LOADER directives must be followed by a YAML document marker (---)")
    if len(directive_lines) > 1:
        raise YamlIncludeError("only one %LOADER directive is allowed")
    return directives, directive_lines


def _strip_loader_directive(text: str) -> tuple[str, str | None]:
    directives, directive_lines = _header_directives(text)
    loader_tags = [item.value for item in directives if item.directive == "LOADER"]
    if not loader_tags:
        return text, None

    lines = text.splitlines(keepends=True)
    line_index = directive_lines[0]
    original = lines[line_index]
    if original.endswith("\r\n"):
        lines[line_index] = "\r\n"
    elif original.endswith("\n"):
        lines[line_index] = "\n"
    elif original.endswith("\r"):
        lines[line_index] = "\r"
    else:
        lines[line_index] = ""
    return "".join(lines), loader_tags[0]


def get_yaml_directives(path_or_file: str | Path | TextIO) -> list[YamlDirective]:
    """Return YAML and package directives preceding the first document."""
    if isinstance(path_or_file, (str, Path)):
        with Path(path_or_file).open("r", encoding="utf-8") as stream:
            text = stream.read()
    else:
        stream = path_or_file
        try:
            initial_pos = stream.tell()
        except (AttributeError, OSError):
            initial_pos = None
        text = stream.read()
        if initial_pos is not None:
            stream.seek(initial_pos)
    directives, _ = _header_directives(text)
    return directives


def _normalize_roots(paths: Iterable[str | Path | IncludeRoot]) -> tuple[IncludeRoot, ...]:
    normalized: list[IncludeRoot] = []
    for value in paths:
        if isinstance(value, MemoryRoot):
            root: IncludeRoot = value
        elif isinstance(value, FileRoot):
            path = Path(value.path).resolve(strict=True)
            if not path.is_dir():
                raise ValueError(f"include root is not a directory: {path}")
            root = FileRoot(path)
        else:
            path = Path(value).resolve(strict=True)
            if not path.is_dir():
                raise ValueError(f"include root is not a directory: {path}")
            root = FileRoot(path)
        if root not in normalized:
            normalized.append(root)
    return tuple(normalized)


def _source_is_within_roots(source: IncludeSource, roots: Iterable[IncludeRoot]) -> bool:
    if isinstance(source, FileSource):
        try:
            canonical = source.path.resolve(strict=True)
        except (OSError, RuntimeError):
            return False
        return canonical.is_file() and any(
            isinstance(root, FileRoot) and canonical.is_relative_to(root.path)
            for root in roots
        )
    return any(isinstance(root, MemoryRoot) and source.root == root for root in roots)


def _include_source_label(source: IncludeSource) -> str:
    if isinstance(source, FileSource):
        return str(source.path)
    return f"memory:{source.root.namespace}/{source.path.as_posix()}"


def _read_bounded(stream: Any, limit: int) -> bytes:
    """Read a binary stream completely up to its limit plus the caller's probe byte."""
    content = bytearray()
    remaining = limit
    while remaining:
        chunk = stream.read(remaining)
        if chunk is None:
            raise OSError("include resolver stream returned no blocking read result")
        if not isinstance(chunk, bytes):
            raise TypeError("include resolver streams must return bytes")
        if len(chunk) > remaining:
            raise ValueError("include resolver stream returned more bytes than requested")
        if not chunk:
            break
        content.extend(chunk)
        remaining -= len(chunk)
    return bytes(content)


def _deep_merge(base: dict[Any, Any], overlay: dict[Any, Any]) -> dict[Any, Any]:
    active: set[tuple[int, int]] = set()

    def merge(left: dict[Any, Any], right: dict[Any, Any]) -> dict[Any, Any]:
        pair = (id(left), id(right))
        if pair in active:
            raise YamlIncludeError("cyclic YAML aliases are not supported during mapping overlays")
        active.add(pair)
        try:
            result = dict(left)
            for key, value in right.items():
                if value is _DELETE:
                    result.pop(key, None)
                elif key in result and isinstance(result[key], dict) and isinstance(value, dict):
                    result[key] = merge(result[key], value)
                else:
                    # Lists are replaced as a whole; null is an explicit value.
                    result[key] = value
            return result
        finally:
            active.remove(pair)

    return merge(base, overlay)


class UniqueKeySafeLoader(SafeLoader):
    """SafeLoader variant that rejects duplicate explicit mapping keys."""

    @staticmethod
    def _is_merge_key(node: Node) -> bool:
        return (
            isinstance(node, ScalarNode)
            and node.tag == _merge_tag
            and node.value == _merge_tag_value
        )

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> YamlDict:
        if not isinstance(node, MappingNode):
            return super().construct_mapping(node, deep=deep)
        seen: set[Hashable] = set()
        for key_node, _ in node.value:
            # PyYAML's legacy anchor merge expansion is handled by SafeLoader;
            # only explicit keys in this source mapping are duplicate-checked.
            if self._is_merge_key(key_node):
                continue
            key = self.construct_object(key_node, deep=True)
            try:
                hash(key)
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found unhashable key {key!r}",
                    key_node.start_mark,
                ) from exc
            if key in seen:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _assert_no_delete(value: Any, seen: set[int] | None = None) -> None:
    if value is _DELETE:
        raise YamlIncludeError("!delete is only valid inside a mapping overlay")
    if seen is None:
        seen = set()
    if isinstance(value, dict):
        if id(value) in seen:
            return
        seen.add(id(value))
        for key, item in value.items():
            _assert_no_delete(key, seen)
            _assert_no_delete(item, seen)
    elif isinstance(value, list):
        if id(value) in seen:
            return
        seen.add(id(value))
        for item in value:
            _assert_no_delete(item, seen)


class IncludeLoader(SafeLoader):
    """SafeLoader adapter for bounded file includes and mapping overlays."""

    def __init__(
        self,
        stream: TextIO,
        *,
        current_file: Path | None,
        search_paths: Iterable[IncludeRoot],
        allowed_roots: Iterable[IncludeRoot],
        recursive_dispatch: bool,
        load_stack: Iterable[IncludeSource],
        budget: _IncludeBudget,
        include_resolver: IncludeResolver | None = None,
        source: IncludeSource | None = None,
    ) -> None:
        super().__init__(stream)
        self.current_file = current_file
        self.search_paths = tuple(search_paths)
        self.allowed_roots = tuple(allowed_roots)
        self.recursive_dispatch = recursive_dispatch
        self.load_stack = tuple(load_stack)
        self.budget = budget
        self.include_resolver = include_resolver or FileSystemIncludeResolver()
        self.source = source or (FileSource(current_file) if current_file is not None else None)

    @staticmethod
    def _construct_include(loader: IncludeLoader, node: Node) -> YamlType:
        return loader._include(node)

    @staticmethod
    def _construct_delete(loader: IncludeLoader, node: Node) -> object:
        if not isinstance(node, ScalarNode) or node.value not in ("", "null", "~"):
            raise ConstructorError(
                "while constructing !delete",
                node.start_mark,
                "!delete does not accept a value",
                node.start_mark,
            )
        return _DELETE

    def _include_error(self, node: Node, message: str, cause: Exception | None = None) -> None:
        error = ConstructorError(
            "while resolving !include",
            None,
            message,
            node.start_mark,
        )
        if cause is not None:
            raise error from cause
        raise error

    def _include(self, node: Node) -> YamlType:
        if not isinstance(node, ScalarNode):
            self._include_error(node, "!include expects a scalar relative path")
        name = self.construct_scalar(node)
        if not isinstance(name, str) or not name:
            self._include_error(node, "!include expects a non-empty path")
        try:
            validate_include_name(name)
        except ValueError as exc:
            self._include_error(node, str(exc), exc)
        try:
            resolved = self.include_resolver.resolve(
                IncludeRequest(
                    name=name,
                    including=self.source,
                    search_paths=self.search_paths,
                    allowed_roots=self.allowed_roots,
                )
            )
        except (OSError, ValueError) as exc:
            self._include_error(node, str(exc), exc)

        try:
            include_source = _canonical_source(resolved.source)
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            self._include_error(node, f"resolver returned an invalid source: {exc}", exc)
        if not _source_is_within_roots(include_source, self.allowed_roots):
            self._include_error(
                node,
                f"include source {_include_source_label(include_source)!r} is outside the allowed roots",
            )
        if include_source in self.load_stack:
            self._include_error(
                node,
                "include cycle detected: "
                + " -> ".join(_include_source_label(item) for item in (*self.load_stack, include_source)),
            )
        if self.budget.current_depth >= self.budget.max_include_depth:
            self._include_error(
                node,
                f"maximum include depth ({self.budget.max_include_depth}) exceeded",
            )

        if self.budget.include_files + 1 > self.budget.max_include_files:
            self._include_error(
                node,
                f"maximum include count ({self.budget.max_include_files}) exceeded",
            )
        remaining_bytes = self.budget.max_total_include_bytes - self.budget.include_bytes
        if remaining_bytes < 0:
            self._include_error(
                node,
                "maximum total included bytes "
                f"({self.budget.max_total_include_bytes}) exceeded",
            )
        read_limit = min(self.budget.max_file_bytes, remaining_bytes)
        try:
            with resolved.open_binary() as stream:
                content = _read_bounded(stream, read_limit + 1)
            if len(content) > self.budget.max_file_bytes:
                self._include_error(
                    node,
                    f"include source {_include_source_label(include_source)} exceeds the per-file size limit "
                    f"({self.budget.max_file_bytes} bytes)",
                )
            if len(content) > remaining_bytes:
                self._include_error(
                    node,
                    "maximum total included bytes "
                    f"({self.budget.max_total_include_bytes}) exceeded",
                )
            self.budget.include_files += 1
            self.budget.include_bytes += len(content)
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                self._include_error(
                    node,
                    f"include source {_include_source_label(include_source)} is not UTF-8 text",
                    exc,
                )
            stream = _NamedStringIO(text, _include_source_label(include_source))
            if self.recursive_dispatch:
                self.budget.current_depth += 1
                try:
                    return load_yaml_dispatch(
                        stream,
                        search_paths=self.search_paths,
                        loader_tag="LoadInclude",
                        recursive_dispatch=True,
                        load_stack=(*self.load_stack, include_source),
                        source=include_source,
                        allowed_roots=self.allowed_roots,
                        include_resolver=self.include_resolver,
                        _budget=self.budget,
                    )
                finally:
                    self.budget.current_depth -= 1
            return yaml.load(stream, Loader=UniqueKeySafeLoader)
        except yaml.YAMLError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            self._include_error(
                node,
                f"cannot read include source {_include_source_label(include_source)}: {exc}",
                exc,
            )

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> YamlDict:
        if not isinstance(node, MappingNode):
            return super().construct_mapping(node, deep=deep)

        explicit: YamlDict = {}
        seen: set[Hashable] = set()
        include_base: dict[Any, Any] | None = None
        merge_seen = False

        for key_node, value_node in node.value:
            if self.is_scalar_merge_node(key_node):
                if merge_seen:
                    raise ConstructorError(
                        "while constructing a mapping overlay",
                        node.start_mark,
                        "only one <<: !include mapping is allowed per mapping",
                        key_node.start_mark,
                    )
                merge_seen = True
                if value_node.tag != "!include":
                    raise ConstructorError(
                        "while constructing a mapping overlay",
                        node.start_mark,
                        "<< is this package's include-overlay operator and must have a !include value",
                        value_node.start_mark,
                    )
                value = self.construct_object(value_node, deep=True)
                if not isinstance(value, dict):
                    raise ConstructorError(
                        "while constructing a mapping overlay",
                        node.start_mark,
                        "the included value for << must be a mapping",
                        value_node.start_mark,
                    )
                include_base = value
                continue

            key = self.construct_object(key_node, deep=True)
            if key is _DELETE:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "!delete cannot be used as a mapping key",
                    key_node.start_mark,
                )
            try:
                hash(key)
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found unhashable key {key!r}",
                    key_node.start_mark,
                ) from exc
            if key in seen:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
            explicit[key] = self.construct_object(value_node, deep=True)

        if include_base is None:
            return explicit
        return _deep_merge(include_base, explicit)

    @staticmethod
    def is_scalar_merge_node(node: Node) -> bool:
        return (
            isinstance(node, ScalarNode)
            and node.tag == _merge_tag
            and node.value == _merge_tag_value
        )


IncludeLoader.add_constructor("!include", IncludeLoader._construct_include)
IncludeLoader.add_constructor("!delete", IncludeLoader._construct_delete)


def _validate_limits(
    max_include_depth: int,
    max_include_files: int,
    max_file_bytes: int,
    max_total_include_bytes: int,
) -> None:
    limits = (max_include_depth, max_include_files, max_file_bytes, max_total_include_bytes)
    if any(not isinstance(value, int) or value < 0 for value in limits):
        raise ValueError("include limits must be non-negative integers")


def _canonical_source(source: IncludeSource) -> IncludeSource:
    if isinstance(source, FileSource):
        return FileSource(Path(source.path).resolve(strict=True))
    if isinstance(source, MemorySource):
        return source
    raise TypeError("include resolvers must return a FileSource or MemorySource")


def load_yaml_dispatch(
    stream: TextIO,
    search_paths: Iterable[str | Path | IncludeRoot] | None = None,
    loader_tag: str | None | UnsetType = UNSET,
    recursive_dispatch: bool = True,
    load_stack: Iterable[Path | IncludeSource] | None = None,
    *,
    source_path: str | Path | None = None,
    source: IncludeSource | None = None,
    allowed_roots: Iterable[str | Path | IncludeRoot] | None = None,
    include_resolver: IncludeResolver | None = None,
    max_include_depth: int = 16,
    max_include_files: int = 64,
    max_file_bytes: int = 1_048_576,
    max_total_include_bytes: int = 8_388_608,
    _budget: _IncludeBudget | None = None,
) -> YamlType:
    """Load ordinary safe YAML or dispatch to bounded local includes.

    A caller-provided resolver is trusted code and must honor the request roots
    without network access. The loader validates returned source identities,
    owns and closes opened byte streams, enforces budgets, and uses SafeLoader.
    Each public call creates a fresh include budget and source stack.
    """
    name = _stream_name(stream)
    source_ref = _source_ref(stream, source_path, source)
    text = stream.read()
    if not isinstance(text, str):
        raise TypeError("YAML input streams must return text")
    cleaned, header_tag = _strip_loader_directive(text)

    effective_tag = header_tag if isinstance(loader_tag, UnsetType) else loader_tag
    if isinstance(source_ref, FileSource):
        source_name = str(source_ref.path)
    elif source_ref is not None:
        source_name = _include_source_label(source_ref)
    else:
        source_name = name
    if effective_tag != "LoadInclude":
        if effective_tag is not None:
            log.warning("Unknown %%LOADER directive: %r, defaulting to safe_loader", effective_tag)
        return yaml.load(_NamedStringIO(cleaned, source_name), Loader=UniqueKeySafeLoader)

    paths = _normalize_roots(search_paths or ())
    if allowed_roots is None:
        roots_input: list[str | Path | IncludeRoot] = list(paths)
        if isinstance(source_ref, FileSource):
            roots_input.insert(0, source_ref.path.parent)
        elif isinstance(source_ref, MemorySource):
            roots_input.insert(0, source_ref.root)
    else:
        roots_input = list(allowed_roots)
    roots = _normalize_roots(roots_input)
    if not roots:
        raise ValueError("include loading requires at least one allowed root")

    _validate_limits(max_include_depth, max_include_files, max_file_bytes, max_total_include_bytes)
    stack: list[IncludeSource] = []
    for item in load_stack or ():
        if isinstance(item, (FileSource, MemorySource)):
            stack.append(_canonical_source(item))
        else:
            stack.append(FileSource(Path(item).resolve(strict=True)))
    if source_ref is not None:
        source_ref = _canonical_source(source_ref)
        if source_ref not in stack:
            stack.append(source_ref)
    if _budget is None:
        seeded_depth = len(stack) - (1 if source_ref is not None and source_ref in stack else 0)
        budget = _IncludeBudget(
            max_include_depth=max_include_depth,
            max_include_files=max_include_files,
            max_file_bytes=max_file_bytes,
            max_total_include_bytes=max_total_include_bytes,
            current_depth=max(0, seeded_depth),
        )
    else:
        budget = _budget
    resolver = include_resolver or FileSystemIncludeResolver()
    loader_stream = _NamedStringIO(cleaned, source_name)
    loader = IncludeLoader(
        loader_stream,
        current_file=source_ref.path if isinstance(source_ref, FileSource) else None,
        source=source_ref,
        search_paths=paths,
        allowed_roots=roots,
        recursive_dispatch=recursive_dispatch,
        load_stack=stack,
        budget=budget,
        include_resolver=resolver,
    )
    try:
        result = loader.get_single_data()
    finally:
        loader.dispose()
    _assert_no_delete(result)
    return result


def load_yaml_include(
    stream: TextIO,
    search_paths: Iterable[str | Path | IncludeRoot] | None = None,
    recursive_dispatch: bool = True,
    load_stack: Iterable[Path | IncludeSource] | None = None,
    *,
    source_path: str | Path | None = None,
    source: IncludeSource | None = None,
    allowed_roots: Iterable[str | Path | IncludeRoot] | None = None,
    include_resolver: IncludeResolver | None = None,
    max_include_depth: int = 16,
    max_include_files: int = 64,
    max_file_bytes: int = 1_048_576,
    max_total_include_bytes: int = 8_388_608,
) -> YamlType:
    """Explicitly load YAML with includes and mapping overlays."""
    return load_yaml_dispatch(
        stream,
        search_paths=search_paths,
        loader_tag="LoadInclude",
        recursive_dispatch=recursive_dispatch,
        load_stack=load_stack,
        source_path=source_path,
        source=source,
        allowed_roots=allowed_roots,
        include_resolver=include_resolver,
        max_include_depth=max_include_depth,
        max_include_files=max_include_files,
        max_file_bytes=max_file_bytes,
        max_total_include_bytes=max_total_include_bytes,
    )


def load_yaml_file(
    path: str | Path,
    search_paths: Iterable[str | Path | IncludeRoot] | None = None,
    *,
    loader_tag: str | None | UnsetType = UNSET,
    allowed_roots: Iterable[str | Path | IncludeRoot] | None = None,
    include_resolver: IncludeResolver | None = None,
    recursive_dispatch: bool = True,
    max_include_depth: int = 16,
    max_include_files: int = 64,
    max_file_bytes: int = 1_048_576,
    max_total_include_bytes: int = 8_388_608,
) -> YamlType:
    """Load a configuration path with the normal dispatch policy."""
    source_path = Path(path).resolve(strict=True)
    if not source_path.is_file():
        raise FileNotFoundError(f"YAML path is not a file: {source_path}")
    with source_path.open("r", encoding="utf-8") as stream:
        return load_yaml_dispatch(
            stream,
            search_paths=search_paths,
            loader_tag=loader_tag,
            recursive_dispatch=recursive_dispatch,
            source_path=source_path,
            allowed_roots=allowed_roots,
            include_resolver=include_resolver,
            max_include_depth=max_include_depth,
            max_include_files=max_include_files,
            max_file_bytes=max_file_bytes,
            max_total_include_bytes=max_total_include_bytes,
        )


class YamlConfigLoader:
    """Reusable loader configuration with independent state for each call."""

    def __init__(
        self,
        *,
        include_resolver: IncludeResolver | None = None,
        search_paths: Iterable[str | Path | IncludeRoot] | None = None,
        allowed_roots: Iterable[str | Path | IncludeRoot] | None = None,
        recursive_dispatch: bool = True,
        max_include_depth: int = 16,
        max_include_files: int = 64,
        max_file_bytes: int = 1_048_576,
        max_total_include_bytes: int = 8_388_608,
    ) -> None:
        _validate_limits(max_include_depth, max_include_files, max_file_bytes, max_total_include_bytes)
        self.include_resolver = include_resolver
        self.search_paths = tuple(search_paths or ())
        self.allowed_roots = tuple(allowed_roots) if allowed_roots is not None else None
        self.recursive_dispatch = recursive_dispatch
        self.max_include_depth = max_include_depth
        self.max_include_files = max_include_files
        self.max_file_bytes = max_file_bytes
        self.max_total_include_bytes = max_total_include_bytes

    def load_file(self, path: str | Path, *, loader_tag: str | None | UnsetType = UNSET) -> YamlType:
        return load_yaml_file(
            path,
            search_paths=self.search_paths,
            loader_tag=loader_tag,
            allowed_roots=self.allowed_roots,
            include_resolver=self.include_resolver,
            recursive_dispatch=self.recursive_dispatch,
            max_include_depth=self.max_include_depth,
            max_include_files=self.max_include_files,
            max_file_bytes=self.max_file_bytes,
            max_total_include_bytes=self.max_total_include_bytes,
        )

    def load_stream(
        self,
        stream: TextIO,
        *,
        source_path: str | Path | None = None,
        source: IncludeSource | None = None,
        loader_tag: str | None | UnsetType = UNSET,
    ) -> YamlType:
        return load_yaml_dispatch(
            stream,
            search_paths=self.search_paths,
            loader_tag=loader_tag,
            recursive_dispatch=self.recursive_dispatch,
            source_path=source_path,
            source=source,
            allowed_roots=self.allowed_roots,
            include_resolver=self.include_resolver,
            max_include_depth=self.max_include_depth,
            max_include_files=self.max_include_files,
            max_file_bytes=self.max_file_bytes,
            max_total_include_bytes=self.max_total_include_bytes,
        )

    def load_include(
        self,
        stream: TextIO,
        *,
        source_path: str | Path | None = None,
        source: IncludeSource | None = None,
    ) -> YamlType:
        return load_yaml_include(
            stream,
            search_paths=self.search_paths,
            recursive_dispatch=self.recursive_dispatch,
            source_path=source_path,
            source=source,
            allowed_roots=self.allowed_roots,
            include_resolver=self.include_resolver,
            max_include_depth=self.max_include_depth,
            max_include_files=self.max_include_files,
            max_file_bytes=self.max_file_bytes,
            max_total_include_bytes=self.max_total_include_bytes,
        )
