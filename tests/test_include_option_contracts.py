"""Shared private concepts preserve their plain-string public boundary."""

from __future__ import annotations

from collections.abc import Callable
from inspect import signature
from typing import cast, get_args

import pytest
import yaml

from yaml_compose import (
    IncludeLoader,
    IncludeRequest,
    YamlConfigLoader,
    load_yaml_compose,
    load_yaml_dispatch,
    load_yaml_file,
)
from yaml_compose._include_specs import parse_include_spec, parse_many_include_spec
from yaml_compose.formats import FormatCapability, FormatDescriptor


def _node(spec: str) -> yaml.Node:
    compose = cast(Callable[[str], yaml.Node | None], yaml.compose)  # pyright: ignore[reportUnknownMemberType]
    node = compose(spec)
    assert node is not None
    return node


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ("", (None, None, False)),
        (", documents: single", (None, None, False)),
        (", documents: all", (None, None, True)),
        (", json_pointer: ''", ("", None, False)),
        (", json_pointer: '/a~1b'", ("/a~1b", None, False)),
        (", documents: all, json_pointer: /0", ("/0", None, True)),
        (", json_path: '$[1:]'", (None, "$[1:]", False)),
        (", documents: all, json_path: '$[1:]'", (None, "$[1:]", True)),
    ],
)
def test_single_and_many_share_selector_and_document_mode_contract(
    options: str, expected: tuple[str | None, str | None, bool]
) -> None:
    single = parse_include_spec(_node("!include {path: 'a#;%20.yaml'" + options + "}"), tag="!include")
    many = parse_many_include_spec(_node("!include-many {paths: ['a#;%20.yaml']" + options + "}"))
    assert single.path == many.sources[0].path == "a#;%20.yaml"
    assert (single.json_pointer, single.json_path, single.all_documents) == expected
    assert (many.json_pointer, many.json_path, many.all_documents) == expected


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ("documents: one", "documents must be 'single' or 'all'"),
        ("documents: true", "documents must be a string"),
        ("json_pointer: '', json_path: '$'", "json_pointer and json_path are mutually exclusive"),
        ("json_pointer: null", "json_pointer must be a string"),
        ("json_path: []", "json_path must be a string"),
        ("json_path: ''", "json_path must be a non-empty string"),
    ],
)
def test_single_and_many_keep_identical_option_errors(options: str, message: str) -> None:
    single_node = _node("!include {path: a.yaml, " + options + "}")
    many_node = _node("!include-many {paths: [a.yaml], " + options + "}")
    with pytest.raises(ValueError) as single:
        parse_include_spec(single_node, tag="!include")
    with pytest.raises(ValueError) as many:
        parse_many_include_spec(many_node)
    assert str(single.value) == str(many.value) == message


@pytest.mark.parametrize(
    "entrypoint",
    [IncludeLoader, IncludeRequest, YamlConfigLoader, load_yaml_compose, load_yaml_dispatch, load_yaml_file],
)
def test_shared_search_default_is_still_the_same_public_string(entrypoint: Callable[..., object]) -> None:
    default: object = signature(entrypoint).parameters["include_search"].default
    assert type(default) is str
    assert default == "local-first"


def test_format_capability_validation_tracks_the_public_literal_contract() -> None:
    assert get_args(FormatCapability) == ("document", "records", "table")
    for capability in get_args(FormatCapability):
        descriptor = FormatDescriptor("example", (".example",), capability=capability)
        assert type(descriptor.capability) is str
        assert descriptor.capability == capability
    with pytest.raises(ValueError, match="format capability must be 'document', 'records', or 'table'"):
        FormatDescriptor("example", (".example",), capability=cast(FormatCapability, "invalid"))
