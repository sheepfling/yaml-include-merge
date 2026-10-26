"""Typed singleton distinguishing omitted arguments from explicit values."""

from __future__ import annotations

from typing import cast

class UnsetType:
    """Sentinel type distinguishing an omitted argument from explicit ``None``."""

    _instance: None | UnsetType = None

    def __new__(cls) -> UnsetType:
        """Return the one module-level instance for stable identity checks."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cast(UnsetType, cls._instance)

UNSET = UnsetType()
