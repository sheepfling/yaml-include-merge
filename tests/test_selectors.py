"""Plain JSON Pointer traversal independent of include IO and local path syntax."""

from __future__ import annotations

from collections import UserDict

import pytest

from yaml_compose._selectors import select_json_pointer


@pytest.mark.parametrize("value", [None, False, 0, "", b"raw", [], {}, object()])
def test_empty_pointer_preserves_root_identity(value: object) -> None:
    assert select_json_pointer(value, "") is value


def test_rfc_6901_examples_and_literal_unicode_percent_characters() -> None:
    value: dict[str, object] = {
        "foo": ["bar", "baz"],
        "": 0,
        "a/b": 1,
        "c%d": 2,
        "e^f": 3,
        "g|h": 4,
        "i\\j": 5,
        'k"l': 6,
        " ": 7,
        "m~n": 8,
        "café": 9,
        "~1": 10,
        "a+b": 11,
        "%2F": 12,
        "#": 13,
        "%XX": 14,
    }
    cases = {
        "/foo": ["bar", "baz"],
        "/foo/0": "bar",
        "/": 0,
        "/a~1b": 1,
        "/c%d": 2,
        "/e^f": 3,
        "/g|h": 4,
        "/i\\j": 5,
        '/k"l': 6,
        "/ ": 7,
        "/m~0n": 8,
        "/café": 9,
        "/~01": 10,
        "/a+b": 11,
        "/%2F": 12,
        "/#": 13,
        "/%XX": 14,
    }
    for pointer, expected in cases.items():
        assert select_json_pointer(value, pointer) == expected


def test_plain_pointer_does_not_percent_decode() -> None:
    value = {"a b": 1, "a%20b": 2, "/": 3, "%2F": 4}
    assert select_json_pointer(value, "/a b") == 1
    assert select_json_pointer(value, "/a%20b") == 2
    assert select_json_pointer(value, "/~1") == 3
    assert select_json_pointer(value, "/%2F") == 4


def test_mapping_keys_are_not_coerced_and_numeric_string_keys_are_not_indexes() -> None:
    assert select_json_pointer({"01": "string", "-": "dash"}, "/01") == "string"
    assert select_json_pointer({"-": "dash"}, "/-") == "dash"
    for key in (0, True, None):
        with pytest.raises(ValueError, match="mapping key"):
            select_json_pointer({key: "not a string"}, f"/{key}")


def test_custom_mapping_and_tuple_values() -> None:
    sentinel = object()
    value = UserDict({"records": ({"value": sentinel},)})
    assert select_json_pointer(value, "/records/0/value") is sentinel


@pytest.mark.parametrize("pointer", ["x", "#", "#/x", "#%2Fx", "/~", "/~2", "/~10~"])
def test_invalid_pointer_is_rejected_with_selector_context(pointer: str) -> None:
    with pytest.raises(ValueError) as error:
        select_json_pointer({}, pointer)
    assert repr(pointer) in str(error.value)


@pytest.mark.parametrize("index", ["-", "-1", "+1", "01", "00", "1.0", " 0", "\u0661", "", "2", "9" * 5000])
def test_invalid_or_out_of_range_array_indexes(index: str) -> None:
    pointer = f"/{index}"
    with pytest.raises(ValueError) as error:
        select_json_pointer(["first", "second"], pointer)
    assert repr(pointer) in str(error.value)


@pytest.mark.parametrize("value", [None, True, 1, 1.5, "abc", b"abc", {"set"}])
def test_scalars_and_sets_cannot_be_traversed(value: object) -> None:
    with pytest.raises(ValueError, match="cannot traverse"):
        select_json_pointer(value, "/0")


def test_missing_empty_and_nested_paths() -> None:
    with pytest.raises(ValueError, match="out of bounds"):
        select_json_pointer([], "/0")
    with pytest.raises(ValueError, match="mapping key"):
        select_json_pointer({"a": {}}, "/a/missing")
    assert select_json_pointer({"": {"": None}}, "//") is None


def test_all_escapes_are_validated_before_traversal() -> None:
    with pytest.raises(ValueError, match="invalid JSON Pointer escape"):
        select_json_pointer({}, "/missing/~2")


def test_finite_pointer_can_traverse_recursive_value() -> None:
    value: list[object] = []
    value.append(value)
    assert select_json_pointer(value, "/0/0") is value
