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

from ._paths import resolve_include_file
from ._unset import UNSET, UnsetType

__all__ = (
    "IncludeLoader",
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


def _normalize_directories(paths: Iterable[str | Path]) -> tuple[Path, ...]:
    normalized: list[Path] = []
    for value in paths:
        path = Path(value).resolve(strict=True)
        if not path.is_dir():
            raise ValueError(f"include root is not a directory: {path}")
        if path not in normalized:
            normalized.append(path)
    return tuple(normalized)


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
        search_paths: Iterable[Path],
        allowed_roots: Iterable[Path],
        recursive_dispatch: bool,
        load_stack: Iterable[Path],
        budget: _IncludeBudget,
    ) -> None:
        super().__init__(stream)
        self.current_file = current_file
        self.search_paths = tuple(search_paths)
        self.allowed_roots = tuple(allowed_roots)
        self.recursive_dispatch = recursive_dispatch
        self.load_stack = tuple(load_stack)
        self.budget = budget

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
            include_file = resolve_include_file(
                name,
                current_file=self.current_file,
                search_paths=self.search_paths,
                allowed_roots=self.allowed_roots,
            )
        except (OSError, ValueError) as exc:
            self._include_error(node, str(exc), exc)

        if include_file in self.load_stack:
            self._include_error(
                node,
                "include cycle detected: " + " -> ".join(map(str, (*self.load_stack, include_file))),
            )
        include_depth = max(0, len(self.load_stack) - 1)
        if include_depth >= self.budget.max_include_depth:
            self._include_error(
                node,
                f"maximum include depth ({self.budget.max_include_depth}) exceeded",
            )

        try:
            size = include_file.stat().st_size
        except OSError as exc:
            self._include_error(node, f"cannot stat include file {include_file}: {exc}", exc)
        if size > self.budget.max_file_bytes:
            self._include_error(
                node,
                f"include file {include_file} exceeds the per-file size limit "
                f"({self.budget.max_file_bytes} bytes)",
            )
        if self.budget.include_files + 1 > self.budget.max_include_files:
            self._include_error(
                node,
                f"maximum include count ({self.budget.max_include_files}) exceeded",
            )
        if self.budget.include_bytes + size > self.budget.max_total_include_bytes:
            self._include_error(
                node,
                "maximum total included bytes "
                f"({self.budget.max_total_include_bytes}) exceeded",
            )
        try:
            with include_file.open("rb") as stream:
                content = stream.read(self.budget.max_file_bytes + 1)
            if len(content) > self.budget.max_file_bytes:
                self._include_error(
                    node,
                    f"include file {include_file} exceeds the per-file size limit "
                    f"({self.budget.max_file_bytes} bytes)",
                )
            if len(content) != size:
                self._include_error(node, f"include file {include_file} changed while being read")
            if self.budget.include_bytes + len(content) > self.budget.max_total_include_bytes:
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
                self._include_error(node, f"include file {include_file} is not UTF-8 text", exc)
            stream = _NamedStringIO(text, str(include_file))
            if self.recursive_dispatch:
                return load_yaml_dispatch(
                    stream,
                    search_paths=(include_file.parent, *self.search_paths),
                    loader_tag="LoadInclude",
                    recursive_dispatch=True,
                    load_stack=(*self.load_stack, include_file),
                    source_path=include_file,
                    allowed_roots=self.allowed_roots,
                    _budget=self.budget,
                )
            return yaml.load(stream, Loader=UniqueKeySafeLoader)
        except yaml.YAMLError:
            raise
        except OSError as exc:
            self._include_error(node, f"cannot read include file {include_file}: {exc}", exc)

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


def load_yaml_dispatch(
    stream: TextIO,
    search_paths: Iterable[Path] | None = None,
    loader_tag: str | None | UnsetType = UNSET,
    recursive_dispatch: bool = True,
    load_stack: Iterable[Path] | None = None,
    *,
    source_path: str | Path | None = None,
    allowed_roots: Iterable[str | Path] | None = None,
    max_include_depth: int = 16,
    max_include_files: int = 64,
    max_file_bytes: int = 1_048_576,
    max_total_include_bytes: int = 8_388_608,
    _budget: _IncludeBudget | None = None,
) -> YamlType:
    """Load ordinary YAML safely or opt into the file-include adapter.

    The package-specific %LOADER directive is removed before the YAML parser
    sees the document. PyYAML's SafeLoader remains the parser and constructor
    base for both paths.
    """
    name = _stream_name(stream)
    source = _source_path(stream, source_path)
    text = stream.read()
    if not isinstance(text, str):
        raise TypeError("YAML input streams must return text")
    cleaned, header_tag = _strip_loader_directive(text)

    effective_tag = header_tag if isinstance(loader_tag, UnsetType) else loader_tag
    if effective_tag != "LoadInclude":
        if effective_tag is not None:
            log.warning("Unknown %%LOADER directive: %r, defaulting to safe_loader", effective_tag)
        return yaml.load(
            _NamedStringIO(cleaned, str(source) if source else name),
            Loader=UniqueKeySafeLoader,
        )

    paths = _normalize_directories(search_paths or ())
    roots_input: Iterable[str | Path]
    if allowed_roots is None:
        roots_input = ([source.parent] if source is not None else []) + list(paths)
    else:
        roots_input = allowed_roots
    roots = _normalize_directories(roots_input)

    stack = tuple(Path(item).resolve(strict=True) for item in (load_stack or ()))
    if source is not None and source not in stack:
        stack = (*stack, source)
    budget = _budget or _IncludeBudget(
        max_include_depth=max_include_depth,
        max_include_files=max_include_files,
        max_file_bytes=max_file_bytes,
        max_total_include_bytes=max_total_include_bytes,
    )
    loader_stream = _NamedStringIO(cleaned, str(source) if source else name)
    loader = IncludeLoader(
        loader_stream,
        current_file=source,
        search_paths=paths,
        allowed_roots=roots,
        recursive_dispatch=recursive_dispatch,
        load_stack=stack,
        budget=budget,
    )
    try:
        result = loader.get_single_data()
    finally:
        loader.dispose()
    _assert_no_delete(result)
    return result


def load_yaml_include(
    stream: TextIO,
    search_paths: Iterable[Path] | None = None,
    recursive_dispatch: bool = True,
    load_stack: Iterable[Path] | None = None,
    *,
    source_path: str | Path | None = None,
    allowed_roots: Iterable[str | Path] | None = None,
    max_include_depth: int = 16,
    max_include_files: int = 64,
    max_file_bytes: int = 1_048_576,
    max_total_include_bytes: int = 8_388_608,
) -> YamlType:
    """Explicitly load YAML with file includes and mapping overlays."""
    return load_yaml_dispatch(
        stream,
        search_paths=search_paths,
        loader_tag="LoadInclude",
        recursive_dispatch=recursive_dispatch,
        load_stack=load_stack,
        source_path=source_path,
        allowed_roots=allowed_roots,
        max_include_depth=max_include_depth,
        max_include_files=max_include_files,
        max_file_bytes=max_file_bytes,
        max_total_include_bytes=max_total_include_bytes,
    )


def load_yaml_file(
    path: str | Path,
    search_paths: Iterable[str | Path] | None = None,
    *,
    loader_tag: str | None | UnsetType = UNSET,
    allowed_roots: Iterable[str | Path] | None = None,
    max_include_depth: int = 16,
    max_include_files: int = 64,
    max_file_bytes: int = 1_048_576,
    max_total_include_bytes: int = 8_388_608,
) -> YamlType:
    """Load a configuration path with the normal dispatch policy."""
    source = Path(path).resolve(strict=True)
    if not source.is_file():
        raise FileNotFoundError(f"YAML path is not a file: {source}")
    with source.open("r", encoding="utf-8") as stream:
        return load_yaml_dispatch(
            stream,
            search_paths=search_paths,
            loader_tag=loader_tag,
            source_path=source,
            allowed_roots=allowed_roots,
            max_include_depth=max_include_depth,
            max_include_files=max_include_files,
            max_file_bytes=max_file_bytes,
            max_total_include_bytes=max_total_include_bytes,
        )
