"""Typed singleton distinguishing omitted arguments from explicit values."""

from __future__ import annotations


class UnsetType:
    """Sentinel type distinguishing an omitted argument from explicit ``None``."""

    _instance: UnsetType | None = None

    def __new__(cls) -> UnsetType:
        """Return the one module-level instance for stable identity checks."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance


UNSET = UnsetType()
