"""Static regression checks for the public sentinel annotation."""

from __future__ import annotations

from typing import cast

from typing_extensions import assert_type

from yaml_compose import UNSET, UnsetType


def check_unset_annotation(value: int | UnsetType | None) -> int | None:
    """Ensure the public sentinel class composes with ordinary optional types."""
    if value is UNSET:
        return 0
    ordinary_value = cast(int | None, value)
    assert_type(ordinary_value, int | None)
    return ordinary_value
