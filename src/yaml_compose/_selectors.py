"""Built-in RFC 6901 Pointer selection, not the JSONPath engine.

This deliberately small exact-address traversal preserves Python values and
requires string mapping keys. It has no expression evaluation or dependency.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import cast

_BAD_POINTER_ESCAPE = re.compile(r"~(?![01])")
_ARRAY_INDEX = re.compile(r"(?:0|[1-9][0-9]*)\Z")


def _pointer_tokens(pointer: str) -> tuple[str, ...]:
    """Validate plain pointer tokens, without URI or percent decoding."""
    if not pointer:
        return ()
    if not pointer.startswith("/"):
        raise ValueError(f"selector {pointer!r} must be an empty JSON Pointer or begin with '/'")
    tokens = pointer[1:].split("/")
    if any(_BAD_POINTER_ESCAPE.search(token) for token in tokens):
        raise ValueError(f"selector {pointer!r} has an invalid JSON Pointer escape; use '~0' or '~1'")
    # RFC 6901 requires this order: '~01' names '~1', not '/'.
    return tuple(token.replace("~1", "/").replace("~0", "~") for token in tokens)


def validate_json_pointer(pointer: str) -> None:
    """Validate the complete pointer before any source is resolved or opened."""
    _pointer_tokens(pointer)


def select_json_pointer(value: object, pointer: str) -> object:
    """Select a value using RFC 6901 tokens without coercing keys or scalar types.

    Mappings use string keys, while lists and tuples use canonical zero-based
    decimal indexes. Root selection preserves any value, including ``None``.
    Missing paths and malformed selectors raise ``ValueError``.
    """
    current = value
    for token in _pointer_tokens(pointer):
        if isinstance(current, Mapping):
            mapping = cast(Mapping[object, object], current)
            if token not in mapping:
                raise ValueError(f"selector {pointer!r}: mapping key {token!r} does not exist")
            current = mapping[token]
        elif isinstance(current, (list, tuple)):
            sequence = cast(list[object] | tuple[object, ...], current)
            if not _ARRAY_INDEX.fullmatch(token):
                raise ValueError(f"selector {pointer!r}: {token!r} is not a canonical non-negative array index")
            # Compare decimal strings before int conversion, bounding conversion
            # work even for indexes exceeding Python's integer-string limit.
            maximum = str(len(sequence) - 1)
            if not sequence or len(token) > len(maximum) or (len(token) == len(maximum) and token > maximum):
                raise ValueError(f"selector {pointer!r}: array index {token!r} is out of bounds")
            current = sequence[int(token)]
        else:
            raise ValueError(f"selector {pointer!r}: cannot traverse {type(current).__name__} with token {token!r}")
    return current
