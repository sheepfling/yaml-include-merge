"""Bounds for included files and the defaults used by new loaders.

``DEFAULT_INCLUDE_LIMITS`` is the immutable, documented baseline. Applications
can inspect the active process-wide value with ``get_default_include_limits``
and replace it with ``set_default_include_limits``. A top-level load captures
one limits snapshot; changing the default does not change an existing loader.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import cast

from ._unset import UNSET, UnsetType


@dataclass(frozen=True, slots=True)
class IncludeLimits:
    """Optional non-negative limits applied to files reached through ``!include``.

    The top-level YAML input is not byte-limited. Each new top-level load starts
    a fresh count. A non-negative integer enforces a bound; zero forbids any
    use of that resource; ``None`` leaves that resource unbounded. Booleans,
    negative numbers, and other values raise ``ValueError``.

    Attributes:
        max_include_depth: Maximum nested ``!include`` levels, or ``None``.
        max_include_files: Maximum ``!include`` operations per top-level load,
            counting repeated loads of the same source, or ``None``.
        max_file_bytes: Maximum encoded bytes read from one included file, or ``None``.
        max_total_include_bytes: Maximum encoded bytes read across all includes,
            or ``None``.

    """

    max_include_depth: int | None = 16
    max_include_files: int | None = 64
    max_file_bytes: int | None = 1_048_576
    max_total_include_bytes: int | None = 8_388_608

    def __post_init__(self) -> None:
        """Reject invalid bounds before a limits object can be used."""
        values: tuple[int | None, ...] = (
            self.max_include_depth,
            self.max_include_files,
            self.max_file_bytes,
            self.max_total_include_bytes,
        )
        if any(value is not None and (type(value) is not int or value < 0) for value in values):
            raise ValueError("include limits must be non-negative integers or None")


DEFAULT_INCLUDE_LIMITS = IncludeLimits()
_configured_default_limits = DEFAULT_INCLUDE_LIMITS
_default_limits_lock = Lock()


def get_default_include_limits() -> IncludeLimits:
    """Return the active defaults for new top-level loads and loader instances.

    The returned ``IncludeLimits`` is immutable. Use ``dataclasses.replace``
    and ``set_default_include_limits`` to change selected fields.
    """
    with _default_limits_lock:
        return _configured_default_limits


def set_default_include_limits(limits: IncludeLimits) -> None:
    """Replace the process-wide defaults for future loads and loader instances.

    Pass an ``IncludeLimits`` instance, usually at application startup. Existing
    ``YamlConfigLoader`` instances and in-progress loads keep their captured
    limits. Per-call ``max_*`` arguments still override individual fields;
    passing ``None`` explicitly disables one bound.
    Pass ``DEFAULT_INCLUDE_LIMITS`` to restore the package baseline. A value
    of another type raises ``TypeError``.
    """
    if type(limits) is not IncludeLimits:
        raise TypeError("limits must be an IncludeLimits instance")
    global _configured_default_limits
    with _default_limits_lock:
        _configured_default_limits = limits


def resolve_include_limits(
    max_include_depth: int | UnsetType | None,
    max_include_files: int | UnsetType | None,
    max_file_bytes: int | UnsetType | None,
    max_total_include_bytes: int | UnsetType | None,
    *,
    include_limits: IncludeLimits | UnsetType = UNSET,
) -> IncludeLimits:
    """Combine a reusable profile and individual overrides into one snapshot.

    An omitted profile uses the current process-wide default. Omitted field
    overrides keep the profile value; explicit ``None`` removes that bound.
    The returned immutable limits object is one snapshot, so later default
    changes cannot alter an in-progress load. Invalid bounds raise
    ``ValueError`` and an invalid profile raises ``TypeError``.
    """
    defaults = get_default_include_limits() if include_limits is UNSET else include_limits
    if not isinstance(defaults, IncludeLimits):
        raise TypeError("include_limits must be an IncludeLimits instance")
    overrides = (
        max_include_depth,
        max_include_files,
        max_file_bytes,
        max_total_include_bytes,
    )
    if all(value is UNSET for value in overrides):
        return defaults
    return IncludeLimits(
        max_include_depth=(
            defaults.max_include_depth
            if max_include_depth is UNSET
            else cast(int | None, max_include_depth)
        ),
        max_include_files=(
            defaults.max_include_files
            if max_include_files is UNSET
            else cast(int | None, max_include_files)
        ),
        max_file_bytes=(
            defaults.max_file_bytes if max_file_bytes is UNSET else cast(int | None, max_file_bytes)
        ),
        max_total_include_bytes=(
            defaults.max_total_include_bytes
            if max_total_include_bytes is UNSET
            else cast(int | None, max_total_include_bytes)
        ),
    )
