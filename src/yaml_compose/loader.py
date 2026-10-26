"""Safe YAML construction with bounded includes and local mapping overlays.

Use ``load_yaml_file`` or ``YamlConfigLoader`` for ordinary application code.
``load_yaml_dispatch`` accepts a text stream and enables ``!include`` when its
header selects ``%LOADER YamlCompose``. The constructors retain PyYAML source
marks so include and mapping errors identify the relevant YAML location.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import NamedTuple, NoReturn, TextIO, cast

import yaml
from yaml import MappingNode, Node, SafeLoader, ScalarNode, SequenceNode
from yaml.constructor import ConstructorError
from yaml.resolver import BaseResolver

from ._overlays import DELETE as _DELETE
from ._overlays import ReplaceMarker as _ReplaceMarker
from ._overlays import SpreadMarker as _SpreadMarker
from ._overlays import YamlDict, YamlIncludeError, YamlType
from ._overlays import assert_no_markers as _assert_no_overlay_markers
from ._overlays import merge_mappings as _merge_mappings
from ._search import IncludeSearch, validate_include_search
from ._streams import read_bounded
from ._unset import UNSET, UnsetType
from ._yaml_loader import configured_include_loader, ordinary_loader, validate_yaml_loader
from .limits import DEFAULT_INCLUDE_LIMITS, IncludeLimits, resolve_include_limits
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
from .tags import (
    COMPOSE_LOADER_TAG,
    DEEP_MERGE_KEY,
    DELETE_TAG,
    INCLUDE_TAG,
    LOADER_DIRECTIVE,
    MERGE_KEY,
    MERGE_TAG,
    REPLACE_TAG,
    SPREAD_TAG,
)

__all__ = (
    "DEFAULT_INCLUDE_LIMITS",
    "IncludeLimits",
    "IncludeLoader",
    "YamlConfigLoader",
    "YamlDirective",
    "YamlIncludeError",
    "load_yaml_compose",
    "load_yaml_dispatch",
    "load_yaml_file",
)

_directive_regex = re.compile(r"^%(?P<directive>[^ \t]+)(?:[ \t]+(?P<value>.*))?$")


@dataclass(frozen=True, slots=True)
class YamlDirective:
    """One header directive with its name and unparsed value text."""

    directive: str
    value: str


class _HeaderDirectives(NamedTuple):
    directives: list[YamlDirective]
    line_numbers: list[int]


class _StrippedLoaderDirective(NamedTuple):
    text: str
    loader_tag: str | None


@dataclass(slots=True)
class _IncludeBudget:
    max_include_depth: int | None
    max_include_files: int | None
    max_file_bytes: int | None
    max_total_include_bytes: int | None
    include_files: int = 0
    include_bytes: int = 0
    current_depth: int = 0


class _NamedStringIO(StringIO):
    def __init__(self, text: str, name: str) -> None:
        super().__init__(text)
        self.name = name


def _read_text_input(stream: TextIO) -> object:
    """Keep the runtime result opaque until its text type has been checked."""
    return stream.read()


def _stream_identity(stream: TextIO) -> tuple[Path | None, str]:
    """Read a stream name once as a normalized file path or a display label."""
    name = getattr(stream, "name", None)
    if not isinstance(name, (str, os.PathLike)):
        return None, "<yaml stream>"
    value = cast(str | bytes, os.fspath(name))
    if not isinstance(value, str) or (value and value.startswith("<") and value.endswith(">")):
        return None, "<yaml stream>"
    path = Path(value).resolve(strict=True)
    return path, str(path)


def _source_ref(
    source_path: Path | None,
    source: IncludeSource | None,
) -> IncludeSource | None:
    if source is not None and source_path is not None:
        raise ValueError("pass either source or source_path, not both")
    if source is not None:
        return _canonical_source(source)
    return FileSource(source_path) if source_path is not None else None


def _header_directives(text: str) -> _HeaderDirectives:
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
            if match.group("directive") == LOADER_DIRECTIVE:
                directive_lines.append(index)
            continue
        in_header = False
    if directive_lines and not document_started:
        raise YamlIncludeError(
            f"%{LOADER_DIRECTIVE} directives must be followed by a YAML document marker (---)"
        )
    if len(directive_lines) > 1:
        raise YamlIncludeError(f"only one %{LOADER_DIRECTIVE} directive is allowed")
    return _HeaderDirectives(directives, directive_lines)


def _strip_loader_directive(text: str) -> _StrippedLoaderDirective:
    directives, directive_lines = _header_directives(text)
    loader_tags = [item.value for item in directives if item.directive == LOADER_DIRECTIVE]
    if not loader_tags:
        return _StrippedLoaderDirective(text, None)
    if loader_tags[0] != COMPOSE_LOADER_TAG:
        raise YamlIncludeError(
            f"unsupported %{LOADER_DIRECTIVE} value {loader_tags[0]!r}; "
            f"expected {COMPOSE_LOADER_TAG!r}"
        )

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
    return _StrippedLoaderDirective("".join(lines), loader_tags[0])


def get_yaml_directives(path_or_file: str | os.PathLike[str] | TextIO) -> list[YamlDirective]:
    """Read directives preceding the first YAML document marker.

    ``path_or_file`` may be a filesystem path, a path-like object, or an open
    text stream. Seekable
    streams are restored to their starting position. A non-seekable stream is
    consumed. Duplicate or unterminated ``%LOADER`` directives raise
    ``YamlIncludeError``.
    """
    if isinstance(path_or_file, (str, os.PathLike)):
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


def _normalize_roots(
    paths: Iterable[Path | IncludeRoot],
) -> tuple[IncludeRoot, ...]:
    normalized: list[IncludeRoot] = []
    for value in paths:
        if isinstance(value, MemoryRoot):
            root: IncludeRoot = value
        elif isinstance(value, FileRoot):
            path = value.path.resolve(strict=True)
            if not path.is_dir():
                raise ValueError(f"include root is not a directory: {path}")
            root = FileRoot(path)
        else:
            path = value.resolve(strict=True)
            if not path.is_dir():
                raise ValueError(f"include root is not a directory: {path}")
            root = FileRoot(path)
        if root not in normalized:
            normalized.append(root)
    return tuple(normalized)


def _source_is_within_roots(source: IncludeSource, roots: Iterable[IncludeRoot]) -> bool:
    if isinstance(source, FileSource):
        return source.path.is_file() and any(
            isinstance(root, FileRoot) and source.path.is_relative_to(root.path) for root in roots
        )
    return any(isinstance(root, MemoryRoot) and source.root == root for root in roots)


def _include_source_label(source: IncludeSource) -> str:
    if isinstance(source, FileSource):
        return str(source.path)
    return f"memory:{source.root.namespace}/{source.path.as_posix()}"


def _construct_object(loader: SafeLoader, node: Node, *, deep: bool = True) -> object:
    """Confine PyYAML's dynamically typed constructor to one boundary."""
    construct = cast(
        Callable[[Node, bool], object],
        loader.construct_object,  # pyright: ignore[reportUnknownMemberType]
    )
    return construct(node, deep)


def _include_error(node: Node, message: str, cause: Exception | None = None) -> NoReturn:
    error = ConstructorError(
        f"while resolving {INCLUDE_TAG}",
        None,
        message,
        node.start_mark,
    )
    if cause is not None:
        raise error from cause
    raise error


def _validate_absolute_include_policy(allow_absolute_includes: object) -> None:
    if not isinstance(allow_absolute_includes, bool):
        raise ValueError("allow_absolute_includes must be a bool")


class IncludeLoader(SafeLoader):
    """Construct bounded includes, shallow or deep mapping merges, and list spreads.

    This lower-level adapter receives normalized roots, source identities, and
    a shared include budget from ``load_yaml_dispatch``. Applications should
    normally use that function or ``YamlConfigLoader`` instead of constructing
    ``IncludeLoader`` directly. ``yaml_loader`` selects a PyYAML loader class
    whose custom constructors and resolvers are also used for nested files.
    """

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
        yaml_loader: type[object] = SafeLoader,
        include_search: IncludeSearch = "local-first",
        allow_absolute_includes: bool = True,
    ) -> None:
        """Initialize one parse with its source, resolver, YAML loader, and budget."""
        validate_include_search(include_search)
        _validate_absolute_include_policy(allow_absolute_includes)
        super().__init__(stream)
        self.current_file = current_file
        self.search_paths = tuple(search_paths)
        self.allowed_roots = tuple(allowed_roots)
        self.recursive_dispatch = recursive_dispatch
        self.load_stack = tuple(load_stack)
        self.budget = budget
        self.yaml_loader = yaml_loader
        self.include_search: IncludeSearch = include_search
        self.allow_absolute_includes = allow_absolute_includes
        self.include_resolver = include_resolver or FileSystemIncludeResolver()
        self.source = source or (FileSource(current_file) if current_file is not None else None)

    @staticmethod
    def _construct_include(loader: IncludeLoader, node: Node) -> YamlType:
        return loader._include(node)

    @staticmethod
    def _construct_delete(loader: IncludeLoader, node: Node) -> object:
        if not isinstance(node, ScalarNode) or node.value != "" or node.style is not None:
            raise ConstructorError(
                f"while constructing {DELETE_TAG}",
                node.start_mark,
                f"{DELETE_TAG} does not accept a value",
                node.start_mark,
            )
        return _DELETE

    @staticmethod
    def _construct_spread(loader: IncludeLoader, node: Node) -> _SpreadMarker:
        if not isinstance(node, SequenceNode) or len(node.value) != 1:
            raise ConstructorError(
                f"while constructing {SPREAD_TAG}",
                None,
                f"{SPREAD_TAG} expects exactly one sequence source in brackets",
                node.start_mark,
            )
        source = _construct_object(loader, node.value[0])
        if not isinstance(source, list):
            raise ConstructorError(
                f"while constructing {SPREAD_TAG}",
                None,
                f"{SPREAD_TAG} source must be a sequence",
                node.value[0].start_mark,
            )
        return _SpreadMarker(cast(list[object], source))

    def _construct_replace(self, node: Node) -> _ReplaceMarker:
        if isinstance(node, MappingNode):
            source = self.construct_mapping(node, deep=True)
        elif isinstance(node, SequenceNode) and len(node.value) == 1:
            source = _construct_object(self, node.value[0])
        else:
            raise ConstructorError(
                f"while constructing {REPLACE_TAG}",
                None,
                f"{REPLACE_TAG} expects a mapping or one mapping source in brackets",
                node.start_mark,
            )
        if not isinstance(source, dict):
            raise ConstructorError(
                f"while constructing {REPLACE_TAG}",
                None,
                f"{REPLACE_TAG} source must be a mapping",
                node.start_mark,
            )
        mapping_source = cast(YamlDict, source)
        _assert_no_overlay_markers(mapping_source)
        return _ReplaceMarker(mapping_source, node.start_mark)

    def construct_sequence(self, node: SequenceNode, deep: bool = False) -> list[object]:
        """Construct completed list values and expand explicitly sourced spreads."""
        values = cast(list[object], super().construct_sequence(node, deep=deep))
        result: list[object] = []
        for value in values:
            if isinstance(value, _SpreadMarker):
                _assert_no_overlay_markers(value.source)
                result.extend(value.source)
            else:
                _assert_no_overlay_markers(value)
                result.append(value)
        return result

    def _include(self, node: Node) -> YamlType:
        if not isinstance(node, ScalarNode):
            _include_error(node, f"{INCLUDE_TAG} expects a scalar path")
        name = self.construct_scalar(node)
        if not name:
            _include_error(node, f"{INCLUDE_TAG} expects a non-empty path")
        try:
            validate_include_name(name, allow_absolute=self.allow_absolute_includes)
        except ValueError as exc:
            _include_error(node, str(exc), exc)
        try:
            resolved = self.include_resolver.resolve(
                IncludeRequest(
                    name=name,
                    including=self.source,
                    include_search=self.include_search,
                    search_paths=self.search_paths,
                    allowed_roots=self.allowed_roots,
                )
            )
        except (OSError, ValueError) as exc:
            _include_error(node, str(exc), exc)

        try:
            include_source = _canonical_source(resolved.source)
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            _include_error(node, f"resolver returned an invalid source: {exc}", exc)
        source_label = _include_source_label(include_source)
        if not _source_is_within_roots(include_source, self.allowed_roots):
            _include_error(
                node,
                f"include source {source_label!r} is outside the allowed roots",
            )
        if include_source in self.load_stack:
            _include_error(
                node,
                "include cycle detected: "
                + " -> ".join(
                    _include_source_label(item) for item in (*self.load_stack, include_source)
                ),
            )
        if (
            self.budget.max_include_depth is not None
            and self.budget.current_depth >= self.budget.max_include_depth
        ):
            _include_error(
                node,
                f"maximum include depth ({self.budget.max_include_depth}) exceeded",
            )

        if (
            self.budget.max_include_files is not None
            and self.budget.include_files + 1 > self.budget.max_include_files
        ):
            _include_error(
                node,
                f"maximum include count ({self.budget.max_include_files}) exceeded",
            )
        remaining_bytes = (
            None
            if self.budget.max_total_include_bytes is None
            else self.budget.max_total_include_bytes - self.budget.include_bytes
        )
        if remaining_bytes is not None and remaining_bytes < 0:
            _include_error(
                node,
                f"maximum total included bytes ({self.budget.max_total_include_bytes}) exceeded",
            )
        active_byte_limits = tuple(
            limit for limit in (self.budget.max_file_bytes, remaining_bytes) if limit is not None
        )
        read_limit = min(active_byte_limits) if active_byte_limits else None
        try:
            with resolved.open_binary() as stream:
                content = read_bounded(stream, None if read_limit is None else read_limit + 1)
            if self.budget.max_file_bytes is not None and len(content) > self.budget.max_file_bytes:
                _include_error(
                    node,
                    f"include source {source_label} exceeds the per-file size limit "
                    f"({self.budget.max_file_bytes} bytes)",
                )
            if remaining_bytes is not None and len(content) > remaining_bytes:
                _include_error(
                    node,
                    "maximum total included bytes "
                    f"({self.budget.max_total_include_bytes}) exceeded",
                )
            self.budget.include_files += 1
            self.budget.include_bytes += len(content)
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                _include_error(
                    node,
                    f"include source {_include_source_label(include_source)} is not UTF-8 text",
                    exc,
                )
            if self.recursive_dispatch:
                self.budget.current_depth += 1
                try:
                    included_text, _ = _strip_loader_directive(text)
                    return _load_composed_text(
                        included_text,
                        source_label,
                        include_source,
                        self.search_paths,
                        self.allowed_roots,
                        recursive_dispatch=self.recursive_dispatch,
                        load_stack=(*self.load_stack, include_source),
                        budget=self.budget,
                        include_resolver=self.include_resolver,
                        yaml_loader=self.yaml_loader,
                        include_search=self.include_search,
                        allow_absolute_includes=self.allow_absolute_includes,
                    )
                finally:
                    self.budget.current_depth -= 1
            ordinary_text, _ = _strip_loader_directive(text)
            return cast(
                YamlType,
                yaml.load(
                    _NamedStringIO(ordinary_text, source_label),
                    Loader=cast(type[SafeLoader], ordinary_loader(self.yaml_loader)),
                ),
            )
        except yaml.YAMLError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            _include_error(
                node,
                f"cannot read include source {_include_source_label(include_source)}: {exc}",
                exc,
            )

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> YamlDict:
        """Apply one shallow ``<<`` or deep ``<<<`` mapping underlay.

        Explicit values override the underlay. Under ``<<<``, two mapping values
        merge recursively unless the local value has ``!replace``. Under ``<<``,
        every local value replaces its source value. In either mode, earlier
        sources in a sequence have higher priority, and explicit local keys win
        regardless of where the merge key appears in the mapping.
        """
        explicit: YamlDict = {}
        seen: set[Hashable] = set()
        # opaque-boundary: Included mappings originate from PyYAML constructors.
        merge_sources: list[YamlDict] | None = None
        merge_deep = False

        for key_node, value_node in node.value:
            is_shallow = self.is_scalar_merge_node(key_node)
            is_deep = self.is_deep_merge_node(key_node)
            if is_shallow or is_deep:
                if merge_sources is not None:
                    raise ConstructorError(
                        "while constructing a mapping overlay",
                        node.start_mark,
                        f"only one {MERGE_KEY} or {DEEP_MERGE_KEY} key is allowed per mapping",
                        key_node.start_mark,
                    )
                merge_deep = is_deep
                merge_sources = []
                source_nodes = (
                    value_node.value if isinstance(value_node, SequenceNode) else [value_node]
                )
                for source_node in source_nodes:
                    value = _construct_object(self, source_node)
                    if not isinstance(value, dict):
                        raise ConstructorError(
                            "while constructing a mapping overlay",
                            node.start_mark,
                            f"each source for {DEEP_MERGE_KEY if is_deep else MERGE_KEY} "
                            "must be a mapping",
                            source_node.start_mark,
                        )
                    merge_sources.append(cast(YamlDict, value))
                continue

            key = _construct_object(self, key_node)
            if key is _DELETE or isinstance(key, (_SpreadMarker, _ReplaceMarker)):
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"{DELETE_TAG}, {REPLACE_TAG}, and {SPREAD_TAG} cannot be used as mapping keys",
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
            value = _construct_object(self, value_node)
            if isinstance(value, _SpreadMarker):
                raise YamlIncludeError(f"{SPREAD_TAG} is only valid as a list item")
            explicit[key] = value

        if merge_sources is None:
            return explicit
        base: YamlDict = {}
        # YAML merge sources are a priority list: earlier sources win.
        for source in reversed(merge_sources):
            base = _merge_mappings(base, source, deep=merge_deep)
        return _merge_mappings(base, explicit, deep=merge_deep)

    @staticmethod
    def is_scalar_merge_node(node: Node) -> bool:
        """Recognize PyYAML's scalar ``<<`` merge key node."""
        return isinstance(node, ScalarNode) and node.tag == MERGE_TAG and node.value == MERGE_KEY

    @staticmethod
    def is_deep_merge_node(node: Node) -> bool:
        """Recognize the package's plain ``<<<`` key, leaving quoted keys literal."""
        return (
            isinstance(node, ScalarNode)
            and node.tag == BaseResolver.DEFAULT_SCALAR_TAG
            and node.value == DEEP_MERGE_KEY
            and node.style is None
        )


IncludeLoader.add_constructor(
    INCLUDE_TAG,
    IncludeLoader._construct_include,  # pyright: ignore[reportPrivateUsage]
)
IncludeLoader.add_constructor(
    DELETE_TAG,
    IncludeLoader._construct_delete,  # pyright: ignore[reportPrivateUsage]
)
IncludeLoader.add_constructor(
    SPREAD_TAG,
    IncludeLoader._construct_spread,  # pyright: ignore[reportPrivateUsage]
)
IncludeLoader.add_constructor(
    REPLACE_TAG,
    IncludeLoader._construct_replace,  # pyright: ignore[reportPrivateUsage]
)


def _canonical_source(source: IncludeSource) -> IncludeSource:
    source_value: object = source
    if isinstance(source_value, FileSource):
        return FileSource(source_value.path.resolve(strict=True))
    if isinstance(source_value, MemorySource):  # pyright: ignore[reportUnnecessaryIsInstance]
        return source_value
    raise TypeError("include resolvers must return a FileSource or MemorySource")


def _load_composed_text(
    text: str,
    source_name: str,
    source: IncludeSource | None,
    search_paths: Iterable[IncludeRoot],
    allowed_roots: Iterable[IncludeRoot],
    *,
    recursive_dispatch: bool,
    load_stack: Iterable[IncludeSource],
    budget: _IncludeBudget,
    include_resolver: IncludeResolver,
    yaml_loader: type[object],
    include_search: IncludeSearch,
    allow_absolute_includes: bool,
) -> YamlType:
    """Parse text with include behavior using already normalized filesystem state."""
    loader = configured_include_loader(yaml_loader, IncludeLoader)(
        _NamedStringIO(text, source_name),
        current_file=source.path if isinstance(source, FileSource) else None,
        source=source,
        search_paths=search_paths,
        allowed_roots=allowed_roots,
        recursive_dispatch=recursive_dispatch,
        load_stack=load_stack,
        budget=budget,
        include_resolver=include_resolver,
        yaml_loader=yaml_loader,
        include_search=include_search,
        allow_absolute_includes=allow_absolute_includes,
    )
    try:
        result = loader.get_single_data()
    finally:
        cast(Callable[[], None], loader.dispose)()  # pyright: ignore[reportUnknownMemberType]
    _assert_no_overlay_markers(result)
    return result


def load_yaml_dispatch(
    stream: TextIO,
    search_paths: Iterable[str | os.PathLike[str] | IncludeRoot] | None = None,
    loader_tag: str | UnsetType | None = UNSET,
    recursive_dispatch: bool = True,
    load_stack: Iterable[str | os.PathLike[str] | IncludeSource] | None = None,
    *,
    source_path: str | os.PathLike[str] | None = None,
    source: IncludeSource | None = None,
    allowed_roots: Iterable[str | os.PathLike[str] | IncludeRoot] | None = None,
    include_resolver: IncludeResolver | None = None,
    yaml_loader: type[object] = SafeLoader,
    include_search: IncludeSearch = "local-first",
    allow_absolute_includes: bool = True,
    include_limits: IncludeLimits | UnsetType = UNSET,
    max_include_depth: int | UnsetType | None = UNSET,
    max_include_files: int | UnsetType | None = UNSET,
    max_file_bytes: int | UnsetType | None = UNSET,
    max_total_include_bytes: int | UnsetType | None = UNSET,
    _budget: _IncludeBudget | None = None,
) -> YamlType:
    """Load YAML with a selected PyYAML loader or dispatch to bounded includes.

    ``stream`` must return text. An omitted ``loader_tag`` follows the header;
    ``COMPOSE_LOADER_TAG`` enables includes explicitly, while ``None`` selects
    ordinary YAML even when a header is present. Unknown tags raise
    ``YamlIncludeError``. ``yaml_loader`` may be ``yaml.SafeLoader``,
    ``yaml.FullLoader``, ``yaml.Loader``, or ``yaml.UnsafeLoader``, or a
    subclass of one; it defaults to ``yaml.SafeLoader``. The selected class
    applies to ordinary documents and every included document. Both paths
    reject duplicate explicit mapping keys; package tags are available only
    when include behavior is selected.
    Caller-supplied constructors are trusted code; select a loader appropriate
    for the trust level of the YAML input.

    ``source_path`` identifies a filesystem stream; ``source`` identifies a
    ``FileSource`` or ``MemorySource``. Pass at most one. The source directory
    and ``search_paths`` form the default allowed roots; explicit
    ``allowed_roots`` replace that default. A stream with includes needs at
    least one allowed root. ``load_stack`` seeds cycle and depth tracking.
    Set ``recursive_dispatch=False`` to parse included files as ordinary YAML
    with ``yaml_loader`` and without resolving their own include tags.

    ``include_search`` controls bare-name lookup: ``local-first`` (the default)
    tries the containing file's directory before the configured search paths;
    ``search-first`` reverses those groups; ``local-only`` and ``search-only``
    restrict lookup to one group. Search paths keep their supplied order. The
    policy is forwarded to every nested include and custom resolver request.
    Explicit ``./`` or ``../`` names require a source and resolve only relative
    to that source, without fallback. Absolute names identify an exact file.
    Search order never changes allowed roots or mapping merge precedence.
    Unknown search policies raise ``ValueError``.
    Set ``allow_absolute_includes=False`` to reject absolute include names,
    including in nested documents. Allowed roots still constrain every include.

    A custom ``include_resolver`` is trusted code and must avoid network access.
    Returned source identities are checked against the allowed roots before
    their streams are opened. The loader closes those streams on every path.
    ``include_limits`` supplies an immutable reusable profile; omitted profiles
    use the active process-wide defaults. Explicit ``max_*`` values override
    only their matching profile fields, and ``None`` disables that bound. Each
    top-level call gets a fresh byte/count budget. Included YAML errors retain
    source marks.
    """
    validate_yaml_loader(yaml_loader)
    validate_include_search(include_search)
    _validate_absolute_include_policy(allow_absolute_includes)
    limits = resolve_include_limits(
        max_include_depth,
        max_include_files,
        max_file_bytes,
        max_total_include_bytes,
        include_limits=include_limits,
    )
    if source is not None and source_path is not None:
        raise ValueError("pass either source or source_path, not both")
    fallback_source_name = "<yaml stream>"
    if source_path is not None:
        normalized_source_path = Path(source_path).resolve(strict=True)
    elif source is None:
        normalized_source_path, fallback_source_name = _stream_identity(stream)
    else:
        normalized_source_path = None
    source_ref = _source_ref(normalized_source_path, source)
    text = _read_text_input(stream)
    if not isinstance(text, str):
        raise TypeError("YAML input streams must return text")
    cleaned, header_tag = _strip_loader_directive(text)

    effective_tag = header_tag if loader_tag is UNSET else loader_tag
    if isinstance(source_ref, FileSource):
        source_name = str(source_ref.path)
    elif source_ref is not None:
        source_name = _include_source_label(source_ref)
    else:
        source_name = fallback_source_name
    if effective_tag != COMPOSE_LOADER_TAG:
        if effective_tag is not None:
            raise YamlIncludeError(
                f"unsupported loader_tag {effective_tag!r}; expected {COMPOSE_LOADER_TAG!r} or None"
            )
        return cast(
            YamlType,
            yaml.load(
                _NamedStringIO(cleaned, source_name),
                Loader=cast(type[SafeLoader], ordinary_loader(yaml_loader)),
            ),
        )

    search_path_inputs: tuple[Path | IncludeRoot, ...] = tuple(
        item if isinstance(item, (FileRoot, MemoryRoot)) else Path(item)
        for item in (search_paths or ())
    )
    paths = _normalize_roots(search_path_inputs)
    if allowed_roots is None:
        roots = paths
        if isinstance(source_ref, FileSource):
            source_root = FileRoot(source_ref.path.parent)
            roots = (source_root, *(root for root in paths if root != source_root))
        elif isinstance(source_ref, MemorySource):
            roots = (source_ref.root, *(root for root in paths if root != source_ref.root))
    else:
        allowed_root_inputs: tuple[Path | IncludeRoot, ...] = tuple(
            item if isinstance(item, (FileRoot, MemoryRoot)) else Path(item)
            for item in allowed_roots
        )
        roots = _normalize_roots(allowed_root_inputs)
    if not roots:
        raise ValueError("include loading requires at least one allowed root")

    stack: list[IncludeSource] = []
    load_stack_inputs: tuple[Path | IncludeSource, ...] = tuple(
        item if isinstance(item, (FileSource, MemorySource)) else Path(item)
        for item in (load_stack or ())
    )
    for item in load_stack_inputs:
        if isinstance(item, (FileSource, MemorySource)):
            stack.append(_canonical_source(item))
        else:
            stack.append(FileSource(item.resolve(strict=True)))
    if source_ref is not None and source_ref not in stack:
        stack.append(source_ref)
    if _budget is None:
        seeded_depth = len(stack) - (1 if source_ref is not None and source_ref in stack else 0)
        budget = _IncludeBudget(
            max_include_depth=limits.max_include_depth,
            max_include_files=limits.max_include_files,
            max_file_bytes=limits.max_file_bytes,
            max_total_include_bytes=limits.max_total_include_bytes,
            current_depth=max(0, seeded_depth),
        )
    else:
        budget = _budget
    resolver = include_resolver or FileSystemIncludeResolver()
    result = _load_composed_text(
        cleaned,
        source_name,
        source_ref,
        paths,
        roots,
        recursive_dispatch=recursive_dispatch,
        load_stack=stack,
        budget=budget,
        include_resolver=resolver,
        yaml_loader=yaml_loader,
        include_search=include_search,
        allow_absolute_includes=allow_absolute_includes,
    )
    return result


def load_yaml_compose(
    stream: TextIO,
    search_paths: Iterable[str | os.PathLike[str] | IncludeRoot] | None = None,
    recursive_dispatch: bool = True,
    load_stack: Iterable[str | os.PathLike[str] | IncludeSource] | None = None,
    *,
    source_path: str | os.PathLike[str] | None = None,
    source: IncludeSource | None = None,
    allowed_roots: Iterable[str | os.PathLike[str] | IncludeRoot] | None = None,
    include_resolver: IncludeResolver | None = None,
    yaml_loader: type[object] = SafeLoader,
    include_search: IncludeSearch = "local-first",
    allow_absolute_includes: bool = True,
    include_limits: IncludeLimits | UnsetType = UNSET,
    max_include_depth: int | UnsetType | None = UNSET,
    max_include_files: int | UnsetType | None = UNSET,
    max_file_bytes: int | UnsetType | None = UNSET,
    max_total_include_bytes: int | UnsetType | None = UNSET,
) -> YamlType:
    """Load a text stream using include and overlay syntax without a header.

    The stream, roots, resolver, and limits have the same meaning as in
    ``load_yaml_dispatch``. This function selects ``COMPOSE_LOADER_TAG`` even if the
    document has no ``%LOADER`` directive.
    """
    return load_yaml_dispatch(
        stream,
        search_paths=search_paths,
        loader_tag=COMPOSE_LOADER_TAG,
        recursive_dispatch=recursive_dispatch,
        load_stack=load_stack,
        source_path=source_path,
        source=source,
        allowed_roots=allowed_roots,
        include_resolver=include_resolver,
        yaml_loader=yaml_loader,
        include_search=include_search,
        allow_absolute_includes=allow_absolute_includes,
        include_limits=include_limits,
        max_include_depth=max_include_depth,
        max_include_files=max_include_files,
        max_file_bytes=max_file_bytes,
        max_total_include_bytes=max_total_include_bytes,
    )


def load_yaml_file(
    path: str | os.PathLike[str],
    search_paths: Iterable[str | os.PathLike[str] | IncludeRoot] | None = None,
    *,
    loader_tag: str | UnsetType | None = UNSET,
    allowed_roots: Iterable[str | os.PathLike[str] | IncludeRoot] | None = None,
    include_resolver: IncludeResolver | None = None,
    yaml_loader: type[object] = SafeLoader,
    include_search: IncludeSearch = "local-first",
    allow_absolute_includes: bool = True,
    recursive_dispatch: bool = True,
    include_limits: IncludeLimits | UnsetType = UNSET,
    max_include_depth: int | UnsetType | None = UNSET,
    max_include_files: int | UnsetType | None = UNSET,
    max_file_bytes: int | UnsetType | None = UNSET,
    max_total_include_bytes: int | UnsetType | None = UNSET,
) -> YamlType:
    """Read one UTF-8 YAML file and dispatch according to its header.

    Composition documents support shallow ``<<`` and recursive ``<<<``
    underlays, with explicit local values winning over inherited values.
    ``include_search`` selects local-first, search-first, local-only, or
    search-only lookup for bare names, and is inherited by nested includes.
    Explicit relative paths resolve only beside their containing file.

    The string or path-like object must name an existing file. Its parent is an allowed
    include root unless ``allowed_roots`` is supplied. ``search_paths`` add
    lookup locations and default allowed roots. An ``include_limits`` profile
    provides reusable bounds; the ``max_*`` values override its matching
    fields and omitted profiles use the active process-wide defaults.
    ``yaml_loader`` accepts
    ``yaml.SafeLoader``, ``yaml.FullLoader``, ``yaml.Loader``, or
    ``yaml.UnsafeLoader`` and applies it to this file and all includes; the
    default is ``yaml.SafeLoader``. File lookup failures raise
    ``OSError``; invalid policies raise ``ValueError``; YAML failures raise
    ``yaml.YAMLError``.
    """
    with open(path, encoding="utf-8") as stream:
        return load_yaml_dispatch(
            stream,
            search_paths=search_paths,
            loader_tag=loader_tag,
            recursive_dispatch=recursive_dispatch,
            allowed_roots=allowed_roots,
            include_resolver=include_resolver,
            yaml_loader=yaml_loader,
            include_search=include_search,
            allow_absolute_includes=allow_absolute_includes,
            include_limits=include_limits,
            max_include_depth=max_include_depth,
            max_include_files=max_include_files,
            max_file_bytes=max_file_bytes,
            max_total_include_bytes=max_total_include_bytes,
        )


class YamlConfigLoader:
    """Reusable loader policy with a fresh include budget for every call.

    Construction captures the active process-wide include limits. Later calls
    to ``set_default_include_limits`` do not change this instance. All methods
    share its resolver, roots, ``include_search``, ``yaml_loader``, and
    per-instance ``max_*`` overrides.
    """

    def __init__(
        self,
        *,
        include_resolver: IncludeResolver | None = None,
        yaml_loader: type[object] = SafeLoader,
        include_search: IncludeSearch = "local-first",
        allow_absolute_includes: bool = True,
        search_paths: Iterable[str | os.PathLike[str] | IncludeRoot] | None = None,
        allowed_roots: Iterable[str | os.PathLike[str] | IncludeRoot] | None = None,
        recursive_dispatch: bool = True,
        include_limits: IncludeLimits | UnsetType = UNSET,
        max_include_depth: int | UnsetType | None = UNSET,
        max_include_files: int | UnsetType | None = UNSET,
        max_file_bytes: int | UnsetType | None = UNSET,
        max_total_include_bytes: int | UnsetType | None = UNSET,
    ) -> None:
        """Capture the resolver, lookup roots, and effective include limits.

        ``search_paths`` supply additional lookup locations. Explicit
        ``allowed_roots`` replace the default root set derived from the source
        and search paths. ``include_resolver`` must implement the trusted local
        ``IncludeResolver`` protocol. ``yaml_loader`` may be one of PyYAML's
        ``SafeLoader``, ``FullLoader``, ``Loader``, or ``UnsafeLoader`` classes
        (or a subclass); custom constructors and resolvers apply to all
        documents loaded by this instance. ``include_limits`` supplies a reusable
        profile, and explicit ``max_*`` values override its matching fields.
        Omitted profiles capture the active process-wide defaults. Explicit
        ``None`` leaves one limit unbounded; invalid limits raise ``ValueError``.
        ``include_search`` accepts ``local-first`` (default), ``search-first``,
        ``local-only``, or ``search-only`` and is passed to every include request.
        It affects bare names only: explicit ``./`` and ``../`` paths require
        a source and never fall back to search paths. Allowed roots still apply
        to every resolved file. An unsupported policy raises ``ValueError``.
        ``allow_absolute_includes=False`` rejects absolute include names.
        """
        limits = resolve_include_limits(
            max_include_depth,
            max_include_files,
            max_file_bytes,
            max_total_include_bytes,
            include_limits=include_limits,
        )
        validate_yaml_loader(yaml_loader)
        validate_include_search(include_search)
        _validate_absolute_include_policy(allow_absolute_includes)
        self.include_resolver = include_resolver
        self.yaml_loader = yaml_loader
        self.include_search: IncludeSearch = include_search
        self.allow_absolute_includes = allow_absolute_includes
        self.search_paths = tuple(search_paths or ())
        self.allowed_roots = tuple(allowed_roots) if allowed_roots is not None else None
        self.recursive_dispatch = recursive_dispatch
        self._include_limits = limits
        self.max_include_depth = limits.max_include_depth
        self.max_include_files = limits.max_include_files
        self.max_file_bytes = limits.max_file_bytes
        self.max_total_include_bytes = limits.max_total_include_bytes

    @property
    def include_limits(self) -> IncludeLimits:
        """Return this loader's immutable effective limits profile."""
        if (
            self.max_include_depth == self._include_limits.max_include_depth
            and self.max_include_files == self._include_limits.max_include_files
            and self.max_file_bytes == self._include_limits.max_file_bytes
            and self.max_total_include_bytes == self._include_limits.max_total_include_bytes
        ):
            return self._include_limits
        return IncludeLimits(
            max_include_depth=self.max_include_depth,
            max_include_files=self.max_include_files,
            max_file_bytes=self.max_file_bytes,
            max_total_include_bytes=self.max_total_include_bytes,
        )

    def load_file(
        self, path: str | os.PathLike[str], *, loader_tag: str | UnsetType | None = UNSET
    ) -> YamlType:
        """Load a UTF-8 file, following its header unless ``loader_tag`` overrides it."""
        return load_yaml_file(
            path,
            search_paths=self.search_paths,
            loader_tag=loader_tag,
            allowed_roots=self.allowed_roots,
            include_resolver=self.include_resolver,
            yaml_loader=self.yaml_loader,
            include_search=self.include_search,
            allow_absolute_includes=self.allow_absolute_includes,
            recursive_dispatch=self.recursive_dispatch,
            include_limits=self.include_limits,
        )

    def load_stream(
        self,
        stream: TextIO,
        *,
        source_path: str | os.PathLike[str] | None = None,
        source: IncludeSource | None = None,
        loader_tag: str | UnsetType | None = UNSET,
    ) -> YamlType:
        """Load a text stream with this instance's policy and header dispatch.

        Pass ``source_path`` for a filesystem stream or ``source`` for a
        resolver-defined identity. They cannot both be supplied.
        """
        return load_yaml_dispatch(
            stream,
            search_paths=self.search_paths,
            loader_tag=loader_tag,
            recursive_dispatch=self.recursive_dispatch,
            source_path=source_path,
            source=source,
            allowed_roots=self.allowed_roots,
            include_resolver=self.include_resolver,
            yaml_loader=self.yaml_loader,
            include_search=self.include_search,
            allow_absolute_includes=self.allow_absolute_includes,
            include_limits=self.include_limits,
        )

    def load_include(
        self,
        stream: TextIO,
        *,
        source_path: str | os.PathLike[str] | None = None,
        source: IncludeSource | None = None,
    ) -> YamlType:
        """Load a text stream with include syntax enabled, even without a header.

        Pass ``source_path`` or ``source`` when relative includes need a base.
        """
        return load_yaml_compose(
            stream,
            search_paths=self.search_paths,
            recursive_dispatch=self.recursive_dispatch,
            source_path=source_path,
            source=source,
            allowed_roots=self.allowed_roots,
            include_resolver=self.include_resolver,
            yaml_loader=self.yaml_loader,
            include_search=self.include_search,
            allow_absolute_includes=self.allow_absolute_includes,
            include_limits=self.include_limits,
        )
