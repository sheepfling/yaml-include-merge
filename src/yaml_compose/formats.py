"""Typed, per-loader adapters for non-YAML included documents.

Format adapters are stateless and safe to share between loader snapshots. They
receive immutable bytes and canonical source identity only; the include loader
owns resolution, confinement, budgets, and stream lifetime.
"""

from __future__ import annotations

import importlib
import json
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import ClassVar, Literal, NoReturn, Protocol, cast

from .resolver import IncludeSource

_FORMAT_ID = re.compile(r"[a-z][a-z0-9_-]*\Z")
FormatCapability = Literal["document", "records"]


@dataclass(frozen=True, slots=True)
class FormatDescriptor:
    """Stable identifier and case-insensitive suffixes for one input format."""

    format_id: str
    suffixes: tuple[str, ...]
    capability: FormatCapability = "document"

    def __post_init__(self) -> None:
        """Validate and normalize the format's suffix aliases."""
        if _FORMAT_ID.fullmatch(self.format_id) is None:
            raise ValueError(
                "format_id must start with a lowercase letter and contain lowercase letters, digits, '_' or '-'"
            )
        suffixes = tuple(suffix.lower() for suffix in self.suffixes)
        if not suffixes or any(
            not suffix.startswith(".") or len(suffix) < 2 or "/" in suffix or "\\" in suffix for suffix in suffixes
        ):
            raise ValueError("format suffixes must be non-empty filename extensions beginning with '.'")
        if len(set(suffixes)) != len(suffixes):
            raise ValueError("format suffixes must be unique")
        if self.capability not in ("document", "records"):
            raise ValueError("format capability must be 'document' or 'records'")
        object.__setattr__(self, "suffixes", suffixes)


@dataclass(frozen=True, slots=True)
class FormatInput:
    """Immutable bounded content and inert identity supplied to an adapter."""

    source: IncludeSource
    source_name: str
    data: bytes


class FormatAdapter(Protocol):
    """Parse one complete input document without resolving or opening sources.

    Adapter instances are shared across loader snapshots, so they must be
    stateless and safe for concurrent calls. Put per-parse state in local
    variables. Expected input failures should raise ``FormatParseError``.
    """

    descriptor: ClassVar[FormatDescriptor]

    def parse(self, source: FormatInput, /) -> object:
        """Return the format's native Python value without coercion."""


class RecordFormatAdapter(Protocol):
    """Parse a bounded record source into an iterator of native values.

    The host consumes and closes this iterator before the YAML include finishes.
    Adapters receive already-read bytes, never a source opener or live stream.
    """

    descriptor: ClassVar[FormatDescriptor]

    def iter_records(self, source: FormatInput, /) -> Iterator[object]:
        """Yield ordered records from one already-bounded source."""
        ...


class FormatParseError(ValueError):
    """Expected invalid-input failure raised by a format adapter."""


class FormatDependencyError(ImportError):
    """An optional parser dependency is unavailable for a selected format."""


class UnknownFormatError(LookupError):
    """The caller explicitly selected a format ID that is not registered."""


@dataclass(frozen=True, slots=True)
class _FormatRegistration:
    descriptor: FormatDescriptor
    adapter: FormatAdapter | RecordFormatAdapter | None


@dataclass(frozen=True, slots=True)
class FormatSelection:
    """Resolved format metadata and parser for one include source.

    ``adapter`` is ``None`` only for the built-in YAML composition path, whose
    node constructors remain managed privately by the host loader.
    """

    descriptor: FormatDescriptor
    adapter: FormatAdapter | RecordFormatAdapter | None


class _TomlParser(Protocol):
    def loads(self, text: str, /) -> object: ...


class _Json5Parser(Protocol):
    def loads(
        self,
        text: str,
        *,
        strict: bool,
        allow_duplicate_keys: bool,
        consume_trailing: bool,
    ) -> object: ...


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Construct a JSON object while rejecting duplicate keys."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _finite_json_float(value: str) -> float:
    """Reject JSON numbers that Python would decode to infinity."""
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"non-finite JSON number {value!r} is not supported")
    return number


def _reject_json_constant(value: str) -> NoReturn:
    """Reject Python's non-standard NaN and Infinity JSON extensions."""
    raise ValueError(f"non-finite JSON constant {value!r} is not supported")


def _parse_strict_json(text: str) -> object:
    return json.loads(
        text,
        object_pairs_hook=_unique_json_object,
        parse_float=_finite_json_float,
        parse_constant=_reject_json_constant,
    )


def _jsonc_location(text: str, offset: int) -> tuple[int, int]:
    """Return one-based line and column for an offset with CR, LF, or CRLF."""
    prefix = text[:offset]
    line = prefix.count("\n") + prefix.count("\r") - prefix.count("\r\n") + 1
    last_break = max(prefix.rfind("\n"), prefix.rfind("\r"))
    return line, offset - last_break


def _mask_jsonc_comments(text: str) -> str:
    """Mask JSONC comments with whitespace without changing source coordinates."""
    chunks: list[str] = []
    chunk_start = 0
    position = 0
    in_string = False
    escaped = False
    while position < len(text):
        character = text[position]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            position += 1
            continue

        if character == '"':
            in_string = True
            position += 1
            continue
        if character != "/" or position + 1 >= len(text):
            position += 1
            continue

        following = text[position + 1]
        if following == "/":
            end = position
            while end < len(text) and text[end] not in "\r\n":
                end += 1
            chunks.extend((text[chunk_start:position], " " * (end - position)))
            chunk_start = end
            position = end
            continue
        if following == "*":
            end = text.find("*/", position + 2)
            if end < 0:
                line, column = _jsonc_location(text, position)
                raise FormatParseError(f"unterminated block comment at line {line}, column {column}")
            end += 2
            comment = text[position:end]
            masked_comment = "".join(character if character in "\r\n" else " " for character in comment)
            chunks.extend((text[chunk_start:position], masked_comment))
            chunk_start = end
            position = end
            continue
        position += 1
    chunks.append(text[chunk_start:])
    return "".join(chunks)


def _toml_parser() -> _TomlParser:
    """Use tomllib or the existing Python 3.10 tomli dependency lazily."""
    try:
        return cast(_TomlParser, importlib.import_module("tomllib"))
    except ModuleNotFoundError as exc:
        if exc.name != "tomllib":
            raise
    try:
        return cast(_TomlParser, importlib.import_module("tomli"))
    except ModuleNotFoundError as exc:
        raise FormatDependencyError("TOML includes on Python before 3.11 require the tomli dependency") from exc


class _StrictJsonAdapter:
    descriptor: ClassVar[FormatDescriptor] = FormatDescriptor("json", (".json",))

    def parse(self, source: FormatInput, /) -> object:
        try:
            text = source.data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FormatParseError(f"include source {source.source_name} is not UTF-8 text") from exc
        try:
            return _parse_strict_json(text)
        except (RecursionError, ValueError) as exc:
            raise FormatParseError(str(exc)) from exc


class _JsoncAdapter:
    descriptor: ClassVar[FormatDescriptor] = FormatDescriptor("jsonc", (".jsonc",))

    def parse(self, source: FormatInput, /) -> object:
        try:
            text = source.data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FormatParseError(f"include source {source.source_name} is not UTF-8 text") from exc
        try:
            return _parse_strict_json(_mask_jsonc_comments(text))
        except FormatParseError:
            raise
        except (RecursionError, ValueError) as exc:
            raise FormatParseError(str(exc)) from exc


def _json5_parser() -> _Json5Parser:
    """Load JSON5 support only when a JSON5 include is selected."""
    try:
        return cast(_Json5Parser, importlib.import_module("json5"))
    except ModuleNotFoundError as exc:
        if exc.name != "json5":
            raise
        raise FormatDependencyError(
            "JSON5 includes require the optional 'json5' dependency; install yaml-compose[json5]"
        ) from exc


class _Json5Adapter:
    descriptor: ClassVar[FormatDescriptor] = FormatDescriptor("json5", (".json5",))

    def parse(self, source: FormatInput, /) -> object:
        try:
            text = source.data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FormatParseError(f"include source {source.source_name} is not UTF-8 text") from exc
        if text.startswith("\ufeff"):
            raise FormatParseError("UTF-8 byte-order mark (BOM) is not supported")
        try:
            parser = _json5_parser()
            return parser.loads(text, strict=True, allow_duplicate_keys=False, consume_trailing=True)
        except FormatDependencyError:
            raise
        except (RecursionError, ValueError) as exc:
            raise FormatParseError(str(exc)) from exc


def _iter_json_records(
    source: FormatInput,
    *,
    format_name: str,
    ignore_blank_lines: bool,
) -> Iterator[object]:
    """Yield strict JSON values split only on LF from bounded UTF-8 bytes."""
    try:
        text = source.data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FormatParseError(f"include source {source.source_name} is not UTF-8 text") from exc
    if text.startswith("\ufeff"):
        raise FormatParseError("UTF-8 byte-order mark (BOM) is not supported")
    if not text:
        return

    records = text.split("\n")
    if text.endswith("\n"):
        records.pop()
    for line_number, record in enumerate(records, start=1):
        if not record.strip(" \t\r"):
            if ignore_blank_lines:
                continue
            raise FormatParseError(f"{format_name} record line {line_number}: blank records are not allowed")
        try:
            value = _parse_strict_json(record)
        except json.JSONDecodeError as exc:
            raise FormatParseError(f"{format_name} record line {line_number}, column {exc.colno}: {exc.msg}") from exc
        except (RecursionError, ValueError) as exc:
            raise FormatParseError(f"{format_name} record line {line_number}: {exc}") from exc
        yield value


class _JsonLinesAdapter:
    descriptor: ClassVar[FormatDescriptor] = FormatDescriptor("jsonl", (".jsonl",), capability="records")

    def iter_records(self, source: FormatInput, /) -> Iterator[object]:
        return _iter_json_records(source, format_name="JSONL", ignore_blank_lines=False)


@dataclass(frozen=True, slots=True)
class NdjsonAdapter:
    """NDJSON parser; blank-line skipping is an explicit opt-in."""

    ignore_blank_lines: bool = False
    descriptor: ClassVar[FormatDescriptor] = FormatDescriptor("ndjson", (".ndjson",), capability="records")

    def __post_init__(self) -> None:
        """Reject truthy non-boolean policies that could obscure configuration."""
        if type(self.ignore_blank_lines) is not bool:
            raise TypeError("ignore_blank_lines must be a bool")

    def iter_records(self, source: FormatInput, /) -> Iterator[object]:
        """Yield strict JSON records using this adapter's blank-line policy."""
        return _iter_json_records(
            source,
            format_name="NDJSON",
            ignore_blank_lines=self.ignore_blank_lines,
        )


class _TomlAdapter:
    descriptor: ClassVar[FormatDescriptor] = FormatDescriptor("toml", (".toml",))

    def parse(self, source: FormatInput, /) -> object:
        try:
            text = source.data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FormatParseError(f"include source {source.source_name} is not UTF-8 text") from exc
        try:
            return _toml_parser().loads(text)
        except FormatDependencyError:
            raise
        except (RecursionError, ValueError) as exc:
            raise FormatParseError(str(exc)) from exc


class FormatRegistry:
    """Mutable registry builder; loaders capture an independent frozen snapshot.

    A new registry contains the private YAML composition entry and the built-in
    strict JSON, JSONC, JSON5, JSONL, NDJSON, and TOML adapters. JSON5 imports its
    optional parser only when selected. Unknown suffixes continue to use YAML.
    Adapter objects must be stateless; snapshots copy lookup maps, retain immutable
    descriptors, and share adapters. Explicit overrides may replace the same format ID only.
    """

    def __init__(self) -> None:
        """Create a mutable registry seeded with the built-in formats."""
        self._formats: dict[str, _FormatRegistration] = {}
        self._suffixes: dict[str, str] = {}
        self._frozen = False
        self._install_yaml()
        self.register(_StrictJsonAdapter())
        self.register(_JsoncAdapter())
        self.register(_Json5Adapter())
        self.register(_JsonLinesAdapter())
        self.register(NdjsonAdapter())
        self.register(_TomlAdapter())

    def _install_yaml(self) -> None:
        descriptor = FormatDescriptor("yaml", (".yaml", ".yml"))
        self._formats[descriptor.format_id] = _FormatRegistration(descriptor, None)
        for suffix in descriptor.suffixes:
            self._suffixes[suffix] = descriptor.format_id

    def register(self, adapter: FormatAdapter | RecordFormatAdapter, *, replace: bool = False) -> None:
        """Register an adapter atomically; suffix collisions require same-ID replacement."""
        if self._frozen:
            raise TypeError("a loader's format registry snapshot is read-only")
        descriptor = getattr(adapter, "descriptor", None)
        parse = getattr(adapter, "parse", None)
        iter_records = getattr(adapter, "iter_records", None)
        if not isinstance(descriptor, FormatDescriptor):
            raise TypeError("adapter must provide a FormatDescriptor")
        if descriptor.capability == "document" and not callable(parse):
            raise TypeError("document adapters must provide a parse(source) method")
        if descriptor.capability == "records" and not callable(iter_records):
            raise TypeError("record adapters must provide an iter_records(source) method")
        format_id = descriptor.format_id
        if format_id == "yaml":
            raise ValueError("the built-in YAML composition entry cannot be replaced")

        current = self._formats.get(format_id)
        if current is not None and not replace:
            raise ValueError(f"format ID {format_id!r} is already registered")

        formats = dict(self._formats)
        suffixes = dict(self._suffixes)
        if current is not None:
            del formats[format_id]
            for suffix in current.descriptor.suffixes:
                if suffixes.get(suffix) == format_id:
                    del suffixes[suffix]
        for suffix in descriptor.suffixes:
            owner = suffixes.get(suffix)
            if owner is not None and owner != format_id:
                raise ValueError(f"suffix {suffix!r} is already owned by format {owner!r}")
            suffixes[suffix] = format_id
        formats[format_id] = _FormatRegistration(descriptor, adapter)
        self._formats = formats
        self._suffixes = suffixes

    def unregister(self, format_id: str) -> None:
        """Remove a registered adapter; the built-in YAML entry is reserved."""
        if self._frozen:
            raise TypeError("a loader's format registry snapshot is read-only")
        if format_id == "yaml":
            raise ValueError("the built-in YAML composition entry cannot be removed")
        try:
            registration = self._formats.pop(format_id)
        except KeyError as exc:
            raise UnknownFormatError(f"format ID {format_id!r} is not registered") from exc
        for suffix in registration.descriptor.suffixes:
            if self._suffixes.get(suffix) == format_id:
                del self._suffixes[suffix]

    def get_format(self, format_id: str) -> FormatDescriptor:
        """Return a registered descriptor or raise ``UnknownFormatError``."""
        try:
            return self._formats[format_id].descriptor
        except KeyError as exc:
            raise UnknownFormatError(f"format ID {format_id!r} is not registered") from exc

    def list_formats(self) -> tuple[FormatDescriptor, ...]:
        """Return registered descriptors in deterministic ID order."""
        return tuple(self._formats[key].descriptor for key in sorted(self._formats))

    def detect_format(self, source: IncludeSource, *, format_id: str | None = None) -> FormatDescriptor:
        """Select an explicit format ID or the adapter for a resolved source suffix."""
        return self.select_format(source, format_id=format_id).descriptor

    def select_format(self, source: IncludeSource, *, format_id: str | None = None) -> FormatSelection:
        """Return the parser selection for a resolved source or explicit format ID.

        An unknown suffix falls back to YAML for backward compatibility. An
        explicitly unknown ID raises ``UnknownFormatError``.
        """
        if format_id is not None:
            try:
                registration = self._formats[format_id]
            except KeyError as exc:
                raise UnknownFormatError(f"format ID {format_id!r} is not registered") from exc
        else:
            selected_id = self._suffixes.get(source.path.suffix.lower(), "yaml")
            registration = self._formats[selected_id]
        return FormatSelection(registration.descriptor, registration.adapter)

    def snapshot(self) -> FormatRegistry:
        """Return an independent, read-only copy for one loader configuration."""
        snapshot = object.__new__(FormatRegistry)
        snapshot._formats = dict(self._formats)
        snapshot._suffixes = dict(self._suffixes)
        snapshot._frozen = True
        return snapshot


__all__ = (
    "FormatAdapter",
    "FormatCapability",
    "FormatDependencyError",
    "FormatDescriptor",
    "FormatInput",
    "FormatParseError",
    "FormatRegistry",
    "FormatSelection",
    "NdjsonAdapter",
    "RecordFormatAdapter",
    "UnknownFormatError",
)
