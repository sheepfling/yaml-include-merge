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
from dataclasses import dataclass
from typing import ClassVar, NoReturn, Protocol, cast

from .resolver import IncludeSource

_FORMAT_ID = re.compile(r"[a-z][a-z0-9_-]*\Z")


@dataclass(frozen=True, slots=True)
class FormatDescriptor:
    """Stable identifier and case-insensitive suffixes for one input format."""

    format_id: str
    suffixes: tuple[str, ...]

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


class FormatParseError(ValueError):
    """Expected invalid-input failure raised by a format adapter."""


class FormatDependencyError(ImportError):
    """An optional parser dependency is unavailable for a selected format."""


class UnknownFormatError(LookupError):
    """The caller explicitly selected a format ID that is not registered."""


@dataclass(frozen=True, slots=True)
class _FormatRegistration:
    descriptor: FormatDescriptor
    adapter: FormatAdapter | None


@dataclass(frozen=True, slots=True)
class FormatSelection:
    """Resolved format metadata and parser for one include source.

    ``adapter`` is ``None`` only for the built-in YAML composition path, whose
    node constructors remain managed privately by the host loader.
    """

    descriptor: FormatDescriptor
    adapter: FormatAdapter | None


class _TomlParser(Protocol):
    def loads(self, text: str, /) -> object: ...


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
    strict JSON and TOML adapters. Unknown suffixes continue to use YAML. Adapter
    objects must be stateless; snapshots copy lookup maps, retain immutable
    descriptors, and share adapters. Explicit overrides may replace the same
    format ID only.
    """

    def __init__(self) -> None:
        """Create a mutable registry seeded with the built-in formats."""
        self._formats: dict[str, _FormatRegistration] = {}
        self._suffixes: dict[str, str] = {}
        self._frozen = False
        self._install_yaml()
        self.register(_StrictJsonAdapter())
        self.register(_TomlAdapter())

    def _install_yaml(self) -> None:
        descriptor = FormatDescriptor("yaml", (".yaml", ".yml"))
        self._formats[descriptor.format_id] = _FormatRegistration(descriptor, None)
        for suffix in descriptor.suffixes:
            self._suffixes[suffix] = descriptor.format_id

    def register(self, adapter: FormatAdapter, *, replace: bool = False) -> None:
        """Register an adapter atomically; suffix collisions require same-ID replacement."""
        if self._frozen:
            raise TypeError("a loader's format registry snapshot is read-only")
        descriptor = getattr(adapter, "descriptor", None)
        parse = getattr(adapter, "parse", None)
        if not isinstance(descriptor, FormatDescriptor) or not callable(parse):
            raise TypeError("adapter must provide a FormatDescriptor and parse(source) method")
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
    "FormatDependencyError",
    "FormatDescriptor",
    "FormatInput",
    "FormatParseError",
    "FormatRegistry",
    "FormatSelection",
    "UnknownFormatError",
)
