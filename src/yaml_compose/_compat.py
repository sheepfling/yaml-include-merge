"""Small Python-version compatibility helpers used by the package."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType

__all__ = ("_TypeVar", "toml_parser_module")

if sys.version_info >= (3, 13):
    from typing import TypeVar as _TypeVar
else:
    from typing_extensions import TypeVar as _TypeVar


def toml_parser_module() -> ModuleType:
    """Return the standard TOML parser or its Python 3.10 backport."""
    try:
        return importlib.import_module("tomllib")
    except ModuleNotFoundError as exc:
        if exc.name != "tomllib":
            raise

    return importlib.import_module("tomli")
