"""Bounds for included files and the defaults used by new loaders.

``DEFAULT_INCLUDE_LIMITS`` is the immutable, documented baseline. Applications
can inspect the active process-wide value with ``get_default_include_limits``
and replace it with ``set_default_include_limits``. A top-level load captures
one limits snapshot; changing the default does not change an existing loader.
"""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass
from threading import Lock

from ._unset import UNSET, UnsetType

def _resolve_limit(value: int | None | UnsetType, default: int | None) -> int | None:
    if isinstance(value, UnsetType):
        return default
    if value is None:
        return None
    if isinstance(value, int):
        return max(0, value)
    raise ValueError("limit must be an integer or None")

_DEFAULT_MAX_INCLUDE_DEPTH = 32
_DEFAULT_MAX_INCLUDE_FILES = 1024
_DEFAULT_MAX_FILE_BYTES = 8_388_608
_DEFAULT_MAX_TOTAL_INCLUDE_BYTES = 67_108_864

@dataclass(slots=True)
class IncludeLimits:
    """Optional non-negative limits applied to files reached through ``!include``.

    The top-level YAML input is not byte-limited. Each new top-level load starts
    a fresh count. A non-negative integer enforces a bound; zero forbids any
    use of that resource; ``None`` leaves that resource unbounded. Booleans,
    negative numbers, and other values raise ``ValueError``.

    These are package policy choices for configuration composition, not YAML
    specification limits. File and byte counts include repeated reads of the
    same source, including reads for spread and replacement values.
    They bound input content, not the size of the constructed Python object graph.

    Attributes:
        max_include_depth: Maximum nested ``!include`` levels, or ``None``.
        max_include_files: Maximum ``!include`` operations per top-level load,
            counting repeated loads of the same source, or ``None``.
        max_file_bytes: Maximum encoded bytes read from one included file, or ``None``.
        max_total_include_bytes: Maximum encoded bytes read across all includes,
            or ``None``.

    """
    max_include_depth: int | None = _DEFAULT_MAX_INCLUDE_DEPTH
    max_include_files: int | None = _DEFAULT_MAX_INCLUDE_FILES
    max_file_bytes: int | None = _DEFAULT_MAX_FILE_BYTES
    max_total_include_bytes: int | None = _DEFAULT_MAX_TOTAL_INCLUDE_BYTES

    def __post_init__(self):
        if self.max_include_depth is not None and self.max_include_depth < 0:
            raise ValueError("max_include_depth must be non-negative")
        if self.max_include_files is not None and self.max_include_files < 0:
            raise ValueError("max_include_files must be non-negative")
        if self.max_file_bytes is not None and self.max_file_bytes < 0:
            raise ValueError("max_file_bytes must be non-negative")
        if self.max_total_include_bytes is not None and self.max_total_include_bytes < 0:
            raise ValueError("max_total_include_bytes must be non-negative")

DEFAULT_INCLUDE_LIMITS = IncludeLimits()
_configured_default_limits = DEFAULT_INCLUDE_LIMITS

def get_default_include_limits() -> IncludeLimits:
    """Return the active defaults for new top-level loads and loader instances.

    The returned ``IncludeLimits`` is immutable. Use ``dataclasses.replace``
    and ``set_default_include_limits`` to change selected fields.
    """
    return copy(_configured_default_limits)

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
    _configured_default_limits = copy(limits)

def resolve_include_limits(
        max_include_depth: int | None | UnsetType,
        max_include_files: int | None | UnsetType,
        max_file_bytes: int | None | UnsetType,
        max_total_include_bytes: int | None | UnsetType,
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
    return IncludeLimits(
        max_include_depth=_resolve_limit(max_include_depth, defaults.max_include_depth),
        max_include_files=_resolve_limit(max_include_files, defaults.max_include_files),
        max_file_bytes=_resolve_limit(max_file_bytes, defaults.max_file_bytes),
        max_total_include_bytes=_resolve_limit(max_total_include_bytes, defaults.max_total_include_bytes),
    )
