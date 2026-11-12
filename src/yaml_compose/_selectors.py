"""URI-fragment JSON Pointers over completed include values."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import cast
from urllib.parse import unquote

_BAD_FRAGMENT_CHARACTER = re.compile(r"[^A-Za-z0-9._~!$&'()*+,;=:@/?%-]")
_BAD_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_BAD_POINTER_ESCAPE = re.compile(r"~(?![01])")
_ARRAY_INDEX = re.compile(r"(?:0|[1-9][0-9]*)\Z")


def split_include_reference(reference: str) -> tuple[str, str | None]:
    """Separate a local path from its optional fragment without decoding the path."""
    path, separator, fragment = reference.partition("#")
    if not path:
        raise ValueError(f"include reference {reference!r} must have a non-empty local path")
    return path, f"#{fragment}" if separator else None


def _pointer_tokens(fragment: str) -> tuple[str, ...]:
    """Decode a URI fragment once, then validate every JSON Pointer token."""
    if not fragment.startswith("#"):
        raise ValueError(f"selector {fragment!r} must be a URI fragment beginning with '#'")
    encoded = fragment[1:]
    if _BAD_FRAGMENT_CHARACTER.search(encoded):
        raise ValueError(f"selector {fragment!r} contains a character requiring URI percent encoding")
    if _BAD_PERCENT_ESCAPE.search(encoded):
        raise ValueError(f"selector {fragment!r} has an invalid percent escape")
    try:
        pointer = unquote(encoded, encoding="utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError(f"selector {fragment!r} is not valid UTF-8") from exc
    if not pointer:
        return ()
    if not pointer.startswith("/"):
        raise ValueError(f"selector {fragment!r} must be empty or begin with '/' after decoding")
    tokens = pointer[1:].split("/")
    if any(_BAD_POINTER_ESCAPE.search(token) for token in tokens):
        raise ValueError(f"selector {fragment!r} has an invalid JSON Pointer escape; use '~0' or '~1'")
    # RFC 6901 requires this order: '~01' names '~1', not '/'.
    return tuple(token.replace("~1", "/").replace("~0", "~") for token in tokens)


def select_json_pointer(value: object, fragment: str) -> object:
    """Select a value using RFC 6901 tokens without coercing keys or scalar types.

    Mappings use string keys, while lists and tuples use canonical zero-based
    decimal indexes. Root selection preserves any value, including ``None``.
    Missing paths and malformed selectors raise ``ValueError``.
    """
    current = value
    for token in _pointer_tokens(fragment):
        if isinstance(current, Mapping):
            mapping = cast(Mapping[object, object], current)
            if token not in mapping:
                raise ValueError(f"selector {fragment!r}: mapping key {token!r} does not exist")
            current = mapping[token]
        elif isinstance(current, (list, tuple)):
            sequence = cast(list[object] | tuple[object, ...], current)
            if not _ARRAY_INDEX.fullmatch(token):
                raise ValueError(f"selector {fragment!r}: {token!r} is not a canonical non-negative array index")
            # Compare decimal strings before int conversion, bounding conversion
            # work even for indexes exceeding Python's integer-string limit.
            maximum = str(len(sequence) - 1)
            if not sequence or len(token) > len(maximum) or (len(token) == len(maximum) and token > maximum):
                raise ValueError(f"selector {fragment!r}: array index {token!r} is out of bounds")
            current = sequence[int(token)]
        else:
            raise ValueError(f"selector {fragment!r}: cannot traverse {type(current).__name__} with token {token!r}")
    return current
