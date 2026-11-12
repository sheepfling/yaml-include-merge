"""Validate inert, tag-specific include options before resolving a source."""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from typing import Literal, get_args

from yaml import MappingNode, Node, ScalarNode, SequenceNode
from yaml.constructor import SafeConstructor
from yaml.resolver import BaseResolver

from ._selectors import validate_json_pointer
from .tags import INCLUDE_RAW_TAG, INCLUDE_TAG, INCLUDE_TEXT_TAG

_OptionName = Literal[
    "path",
    "sources",
    "glob",
    "documents",
    "json_pointer",
    "json_path",
    "encoding",
    "lines",
    "start",
    "end",
    "offset",
    "length",
]
_DocumentMode = Literal["single", "all"]
_DOCUMENT_MODES: tuple[str, ...] = get_args(_DocumentMode)
_SELECTOR_OPTIONS: frozenset[_OptionName] = frozenset(("json_pointer", "json_path"))


@dataclass(frozen=True, slots=True)
class IncludeSpec:
    """Normalized options for one literal source path and explicit selection."""

    path: str
    json_pointer: str | None = None
    json_path: str | None = None
    encoding: str = "utf-8"
    line_start: int = 1
    line_end: int | None = None
    select_lines: bool = False
    offset: int = 0
    length: int | None = None
    all_documents: bool = False


def _mapping(
    node: Node, *, name: str, allowed: frozenset[_OptionName], tagged: bool = False
) -> dict[_OptionName, Node]:
    """Read option nodes without evaluating YAML tags, merges, or expressions."""
    if not isinstance(node, MappingNode) or (not tagged and node.tag != BaseResolver.DEFAULT_MAPPING_TAG):
        raise ValueError(f"{name} must be a mapping")
    result: dict[_OptionName, Node] = {}
    for key, value in node.value:
        if not isinstance(key, ScalarNode) or key.tag != BaseResolver.DEFAULT_SCALAR_TAG:
            raise ValueError(f"{name} option names must be plain strings; YAML merges are not supported")
        if key.value in result:
            raise ValueError(f"duplicate {name} option {key.value!r}")
        if key.value not in allowed:
            raise ValueError(f"unknown {name} option {key.value!r}")
        result[key.value] = value
    return result


def _string(node: Node, *, name: str, empty: bool = False) -> str:
    """Accept string scalar values without constructing custom YAML tags."""
    if not isinstance(node, ScalarNode) or node.tag != BaseResolver.DEFAULT_SCALAR_TAG:
        raise ValueError(f"{name} must be a string")
    if not empty and not node.value:
        raise ValueError(f"{name} must be a non-empty string")
    return node.value


def _integer(node: Node, *, name: str, minimum: int) -> int:
    """Accept YAML integers, but never booleans or implicitly coerced strings."""
    requirement = "positive" if minimum == 1 else "non-negative"
    if not isinstance(node, ScalarNode) or node.tag != "tag:yaml.org,2002:int":
        raise ValueError(f"{name} must be a {requirement} integer")
    try:
        value = SafeConstructor().construct_yaml_int(node)
    except (ValueError, IndexError) as exc:
        raise ValueError(f"{name} must be a {requirement} integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be a {requirement} integer")
    return value


def _all_documents(options: dict[_OptionName, Node]) -> bool:
    """Validate the shared, explicit YAML stream mode without constructing tags."""
    documents = _string(options["documents"], name="documents") if "documents" in options else "single"
    if documents not in _DOCUMENT_MODES:
        raise ValueError("documents must be 'single' or 'all'")
    return documents == "all"


def _json_selectors(options: dict[_OptionName, Node]) -> tuple[str | None, str | None]:
    """Validate the same selector contract for single and collected sources."""
    if "json_pointer" in options and "json_path" in options:
        raise ValueError("json_pointer and json_path are mutually exclusive")
    pointer = _string(options["json_pointer"], name="json_pointer", empty=True) if "json_pointer" in options else None
    if pointer is not None:
        validate_json_pointer(pointer)
    query = _string(options["json_path"], name="json_path") if "json_path" in options else None
    return pointer, query


def parse_include_spec(node: Node, *, tag: str) -> IncludeSpec:
    """Validate inert mapping fields that are meaningful to the given include tag."""
    allowed: set[_OptionName] = {"path"}
    if tag == INCLUDE_TEXT_TAG:
        allowed.update(("encoding", "lines"))
    elif tag == INCLUDE_RAW_TAG:
        allowed.update(("offset", "length"))
    elif tag == INCLUDE_TAG:
        allowed.update(_SELECTOR_OPTIONS)
        allowed.add("documents")
    if not isinstance(node, MappingNode):
        raise ValueError(f"{tag} expects a scalar path or an options mapping")
    options = _mapping(node, name=tag, allowed=frozenset(allowed), tagged=True)
    if "path" not in options:
        raise ValueError(f"{tag} requires a path option")
    path = _string(options["path"], name=f"{tag} path")
    all_documents = _all_documents(options) if tag == INCLUDE_TAG else False

    if tag == INCLUDE_RAW_TAG:
        offset = _integer(options["offset"], name="offset", minimum=0) if "offset" in options else 0
        length = _integer(options["length"], name="length", minimum=0) if "length" in options else None
        return IncludeSpec(path, offset=offset, length=length)

    if tag == INCLUDE_TEXT_TAG:
        encoding = _string(options["encoding"], name="encoding") if "encoding" in options else "utf-8"
        try:
            codec = codecs.lookup(encoding)
            # Use the same registry distinction as bytes.decode, without
            # executing a binary transform merely to validate its name.
            if not getattr(codec, "_is_text_encoding", True):
                raise ValueError(f"encoding {encoding!r} is not a supported text encoding")
        except (LookupError, TypeError, UnicodeError) as exc:
            raise ValueError(f"encoding {encoding!r} is not a supported text encoding") from exc
        start, end = 1, None
        if "lines" in options:
            lines = _mapping(options["lines"], name="lines", allowed=frozenset(("start", "end")))
            start = _integer(lines["start"], name="lines.start", minimum=1) if "start" in lines else 1
            end = _integer(lines["end"], name="lines.end", minimum=1) if "end" in lines else None
            if end is not None and end < start:
                raise ValueError("lines.end must be greater than or equal to lines.start")
        return IncludeSpec(path, encoding=encoding, line_start=start, line_end=end, select_lines="lines" in options)

    pointer, query = _json_selectors(options)
    return IncludeSpec(path, json_pointer=pointer, json_path=query, all_documents=all_documents)


@dataclass(frozen=True, slots=True)
class ManySource:
    """One inert literal path or explicit filesystem pattern and its source mark."""

    path: str
    glob: bool
    node: Node


@dataclass(frozen=True, slots=True)
class ManyIncludeSpec:
    """Shared selection options applied once to each completed source result."""

    sources: tuple[ManySource, ...]
    all_documents: bool = False
    json_pointer: str | None = None
    json_path: str | None = None


def parse_many_include_spec(node: Node) -> ManyIncludeSpec:
    """Validate an object-only source collection without constructing custom tags."""
    options = _mapping(
        node,
        name=INCLUDE_TAG,
        allowed=frozenset[_OptionName](("path", "sources", "documents")) | _SELECTOR_OPTIONS,
        tagged=True,
    )
    if "path" in options or "sources" not in options:
        raise ValueError("!include requires exactly one of path or sources")
    paths = options["sources"]
    if not isinstance(paths, SequenceNode) or paths.tag != BaseResolver.DEFAULT_SEQUENCE_TAG:
        raise ValueError("sources must be a sequence of literal paths or explicit glob mappings")
    if not paths.value:
        raise ValueError("sources must be a non-empty sequence")
    sources: list[ManySource] = []
    for entry in paths.value:
        if isinstance(entry, MappingNode):
            source = _mapping(entry, name="source", allowed=frozenset(("path", "glob")))
            if len(source) != 1:
                raise ValueError("each source mapping must contain exactly one path or glob")
            key = next(iter(source))
            sources.append(ManySource(_string(source[key], name=key), key == "glob", source[key]))
        else:
            sources.append(ManySource(_string(entry, name="path"), False, entry))
    all_documents = _all_documents(options)
    pointer, query = _json_selectors(options)
    return ManyIncludeSpec(tuple(sources), all_documents, pointer, query)
