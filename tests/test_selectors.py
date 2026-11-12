"""Strict JSON Pointer decoding and value traversal independent of include IO."""

from __future__ import annotations

from collections import UserDict

import pytest

from yaml_compose._selectors import select_json_pointer, split_include_reference


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("data.yaml", ("data.yaml", None)),
        ("data.yaml#", ("data.yaml", "#")),
        ("data.yaml#/a", ("data.yaml", "#/a")),
        ("C:\\config\\data.yaml#/a", ("C:\\config\\data.yaml", "#/a")),
        ("./a%20b.yaml#/a%20b", ("./a%20b.yaml", "#/a%20b")),
        ("data.yaml#/a#b", ("data.yaml", "#/a#b")),
    ],
)
def test_split_preserves_the_local_path(reference: str, expected: tuple[str, str | None]) -> None:
    assert split_include_reference(reference) == expected


@pytest.mark.parametrize("value", [None, False, 0, "", b"raw", [], {}, object()])
def test_empty_pointer_preserves_root_identity(value: object) -> None:
    assert select_json_pointer(value, "#") is value


def test_rfc_6901_examples_and_utf8_percent_decoding() -> None:
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
    }
    cases = {
        "#/foo": ["bar", "baz"],
        "#/foo/0": "bar",
        "#/": 0,
        "#/a~1b": 1,
        "#/c%25d": 2,
        "#/e%5Ef": 3,
        "#/g%7Ch": 4,
        "#/i%5Cj": 5,
        "#/k%22l": 6,
        "#/%20": 7,
        "#/m~0n": 8,
        "#/caf%C3%A9": 9,
        "#/%7E01": 10,
        "#/a+b": 11,
        "#/%252F": 12,
        "#/%23": 13,
        "#%2Ffoo%2F1": "baz",
    }
    for fragment, expected in cases.items():
        assert select_json_pointer(value, fragment) == expected


def test_mapping_keys_are_not_coerced_and_numeric_string_keys_are_not_indexes() -> None:
    assert select_json_pointer({"01": "string", "-": "dash"}, "#/01") == "string"
    assert select_json_pointer({"-": "dash"}, "#/-") == "dash"
    for key in (0, True, None):
        with pytest.raises(ValueError, match="mapping key"):
            select_json_pointer({key: "not a string"}, f"#/{key}")


def test_custom_mapping_and_tuple_values() -> None:
    sentinel = object()
    value = UserDict({"records": ({"value": sentinel},)})
    assert select_json_pointer(value, "#/records/0/value") is sentinel


@pytest.mark.parametrize(
    "fragment", ["/x", "", "#name", "#%61", "#/%", "#/%A", "#/%GG", "#/%FF", "#/%C3", "#/~", "#/~2", "#/~10~"]
)
def test_invalid_fragment_is_rejected_with_selector_context(fragment: str) -> None:
    with pytest.raises(ValueError) as error:
        select_json_pointer({}, fragment)
    assert repr(fragment) in str(error.value)


@pytest.mark.parametrize("index", ["-", "-1", "+1", "01", "00", "1.0", " 0", "\u0661", "", "2", "9" * 5000])
def test_invalid_or_out_of_range_array_indexes(index: str) -> None:
    fragment = f"#/{index}"
    with pytest.raises(ValueError) as error:
        select_json_pointer(["first", "second"], fragment)
    assert repr(fragment) in str(error.value)


@pytest.mark.parametrize("value", [None, True, 1, 1.5, "abc", b"abc", {"set"}])
def test_scalars_and_sets_cannot_be_traversed(value: object) -> None:
    with pytest.raises(ValueError, match="cannot traverse"):
        select_json_pointer(value, "#/0")


def test_missing_empty_and_nested_paths() -> None:
    with pytest.raises(ValueError, match="out of bounds"):
        select_json_pointer([], "#/0")
    with pytest.raises(ValueError, match="mapping key"):
        select_json_pointer({"a": {}}, "#/a/missing")
    assert select_json_pointer({"": {"": None}}, "#//") is None


def test_all_escapes_are_validated_before_traversal() -> None:
    with pytest.raises(ValueError, match="invalid JSON Pointer escape"):
        select_json_pointer({}, "#/missing/~2")


def test_finite_pointer_can_traverse_recursive_value() -> None:
    value: list[object] = []
    value.append(value)
    assert select_json_pointer(value, "#/0/0") is value


@pytest.mark.parametrize("reference", ["", "#", "#/a"])
def test_fragment_only_references_are_rejected(reference: str) -> None:
    with pytest.raises(ValueError, match="non-empty local path"):
        split_include_reference(reference)


def test_all_unescaped_uri_fragment_characters() -> None:
    key = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._!$&'()*+,;=:@?"
    assert select_json_pointer({key: 7}, f"#/{key}") == 7
    assert select_json_pointer({"~": 8}, "#/~0") == 8


@pytest.mark.parametrize(
    "character",
    ["#", " ", "\t", "\n", "\r", "\x00", "\x7f", "é", '"', "[", "]", "\\", "{", "}", "|", "^", "`", "<", ">"],
)
def test_uri_fragment_requires_percent_encoding_for_other_characters(character: str) -> None:
    fragment = f"#/{character}"
    with pytest.raises(ValueError, match="percent encoding") as error:
        select_json_pointer({character: 1}, fragment)
    assert repr(fragment) in str(error.value)


def test_percent_encoded_control_and_reserved_keys_are_valid() -> None:
    values = {"\x00": 1, "#": 2, "[": 3, "\\": 4, "é": 5, " ": 6}
    for fragment, expected in [("#/%00", 1), ("#/%23", 2), ("#/%5B", 3), ("#/%5C", 4), ("#/%C3%A9", 5), ("#/%20", 6)]:
        assert select_json_pointer(values, fragment) == expected
