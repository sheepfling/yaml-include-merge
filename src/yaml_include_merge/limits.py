"""Immutable include-resource settings and the library-wide defaults."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IncludeLimits:
    """Resource limits applied to included YAML sources.

    These settings are immutable and safe to share between loader instances.
    Per-load counters are maintained separately by the loading operation.
    """

    max_include_depth: int = 16
    max_include_files: int = 64
    max_file_bytes: int = 1_048_576
    max_total_include_bytes: int = 8_388_608

    def __post_init__(self) -> None:
        values = (
            self.max_include_depth,
            self.max_include_files,
            self.max_file_bytes,
            self.max_total_include_bytes,
        )
        if any(not isinstance(value, int) or value < 0 for value in values):
            raise ValueError("include limits must be non-negative integers")


DEFAULT_INCLUDE_LIMITS = IncludeLimits()
"""Shared immutable package defaults; applications can inspect or copy them."""

