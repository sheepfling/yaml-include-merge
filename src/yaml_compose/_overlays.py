"""Mapping composition and validation shared by YAML merge keys and overlays.

Merge keys combine completed mappings by source priority and apply explicit
local values over the inherited mapping. Mapping operations copy their inputs
instead of modifying the original sources.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping, MutableSet
from dataclasses import dataclass
from datetime import date, time
from typing import cast

import yaml
from yaml.constructor import ConstructorError
from yaml.error import Mark

from .tags import DELETE_TAG, REPLACE_TAG, SPREAD_TAG

YamlDict = dict[Hashable, object]
YamlType = YamlDict | list[object] | str | int | float | bool | None | date | time
DELETE = object()


class YamlIncludeError(yaml.YAMLError, ValueError):
    """YAML error raised for invalid loader selection, includes, or overlays."""


@dataclass(frozen=True, slots=True)
class SpreadMarker:
    """Sequence items waiting for insertion into their containing list."""

    source: list[object]


@dataclass(frozen=True, slots=True)
class ReplaceMarker:
    """A complete mapping that replaces one value during a deep overlay."""

    source: YamlDict
    start_mark: Mark


def assert_no_markers(value: object, seen: MutableSet[int] | None = None) -> None:
    """Reject unapplied operations in a completed value, including cyclic data."""
    if value is DELETE:
        raise YamlIncludeError(f"{DELETE_TAG} is only valid inside a mapping overlay")
    if isinstance(value, SpreadMarker):
        raise YamlIncludeError(f"{SPREAD_TAG} is only valid as a list item")
    if isinstance(value, ReplaceMarker):
        raise ConstructorError(
            f"while applying {REPLACE_TAG}",
            None,
            f"{REPLACE_TAG} is only valid in a deep mapping overlay",
            value.start_mark,
        )
    if seen is None:
        seen = set()
    if isinstance(value, dict):
        mapping = cast(YamlDict, value)
        if id(mapping) in seen:
            return
        seen.add(id(mapping))
        for key, item in mapping.items():
            assert_no_markers(key, seen)
            assert_no_markers(item, seen)
    elif isinstance(value, (list, tuple)):
        sequence = cast(list[object] | tuple[object, ...], value)
        if id(sequence) in seen:
            return
        seen.add(id(sequence))
        for item in sequence:
            assert_no_markers(item, seen)


# opaque-boundary: PyYAML values may contain arbitrary nested mappings.
def merge_mappings(
    base: Mapping[Hashable, object],
    overlay: Mapping[Hashable, object],
    *,
    deep: bool,
) -> YamlDict:
    """Apply local overrides, optionally recursing into inherited mappings."""
    active: set[tuple[int, int]] = set()

    # opaque-boundary: PyYAML values may contain arbitrary nested mappings.
    def merge(left: Mapping[Hashable, object], right: Mapping[Hashable, object]) -> YamlDict:
        pair = (id(left), id(right))
        if pair in active:
            raise YamlIncludeError("cyclic YAML aliases are not supported during mapping overlays")
        active.add(pair)
        try:
            result = dict(left)
            for key, value in right.items():
                if value is DELETE:
                    result.pop(key, None)
                elif isinstance(value, ReplaceMarker):
                    if not deep:
                        raise ConstructorError(
                            f"while applying {REPLACE_TAG}",
                            None,
                            f"{REPLACE_TAG} is only valid in a deep mapping overlay",
                            value.start_mark,
                        )
                    result[key] = value.source
                elif deep and isinstance(result.get(key), dict) and isinstance(value, dict):
                    result[key] = merge(cast(YamlDict, result[key]), cast(YamlDict, value))
                else:
                    result[key] = value
            return result
        finally:
            active.remove(pair)

    return merge(base, overlay)
