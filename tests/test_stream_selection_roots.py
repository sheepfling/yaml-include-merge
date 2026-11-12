"""Explicit YAML stream lists remain the root for indexing and slicing."""

from __future__ import annotations

import json
from io import StringIO

import pytest
import yaml

from yaml_compose import JsonPathLimits, MemoryIncludeResolver, MemoryRoot, load_yaml_compose


def _load_stream(content: str, query: str, *, many: bool, recursive: bool = True) -> object:
    root = MemoryRoot("stream-selection")
    options = (
        "!include {sources: [bundle.yaml], documents: all" if many else "!include {documents: all, path: bundle.yaml"
    )
    return load_yaml_compose(
        StringIO(options + ", json_path: " + json.dumps(query) + "}"),
        allowed_roots=[root],
        include_resolver=MemoryIncludeResolver(root, {"bundle.yaml": content}),
        recursive_dispatch=recursive,
    )


def _require_engine() -> None:
    pytest.importorskip("jsonpath", reason="optional python-jsonpath engine is not installed")
    pytest.importorskip("regex", reason="optional strict JSONPath regex dependency is not installed")
    pytest.importorskip("iregexp_check", reason="optional strict JSONPath regex validator is not installed")


@pytest.mark.parametrize("many", [False, True])
@pytest.mark.parametrize("recursive", [False, True])
@pytest.mark.parametrize(
    ("content", "documents"),
    [
        ("", []),
        ("# empty stream\n", []),
        ("---\n", [None]),
        ("x: 1\n", [{"x": 1}]),
        ("[1, 2]\n", [[1, 2]]),
        ("x: 1\n---\nx: 2\n", [{"x": 1}, {"x": 2}]),
        ("[1, 2]\n---\n[3, 4]\n", [[1, 2], [3, 4]]),
    ],
)
def test_jsonpath_receives_one_complete_stream_list(
    monkeypatch: pytest.MonkeyPatch, content: str, documents: list[object], many: bool, recursive: bool
) -> None:
    calls: list[tuple[object, str]] = []

    def select(value: object, query: str, limits: JsonPathLimits) -> list[object]:
        calls.append((value, query))
        return [value]

    monkeypatch.setattr("yaml_compose.loader.select_jsonpath", select)
    expected = [[documents]] if many else [documents]
    assert _load_stream(content, "$", many=many, recursive=recursive) == expected
    assert calls == [(documents, "$")]


@pytest.mark.parametrize("many", [False, True])
@pytest.mark.parametrize(
    ("content", "query", "selected"),
    [
        ("", "$[:]", []),
        ("---\n", "$[:]", [None]),
        ("x: 1\n", "$[:]", [{"x": 1}]),
        ("x: 1\n", "$", [[{"x": 1}]]),
        ("[1, 2]\n", "$[:]", [[1, 2]]),
        ("x: 0\n---\nx: 1\n---\nx: 2\n---\nx: 3\n", "$[1:3]", [{"x": 1}, {"x": 2}]),
        ("x: 0\n---\nx: 1\n---\nx: 2\n---\nx: 3\n", "$[::-2]", [{"x": 3}, {"x": 1}]),
        ("x: 0\n---\nx: 1\n", "$[-1:]", [{"x": 1}]),
        ("x: 0\n---\nx: 1\n", "$[1:1]", []),
        ("[0, 1]\n---\n[2, 3]\n", "$[1:]", [[2, 3]]),
        ("[0, 1]\n---\n[2, 3]\n", "$[1:][0]", [2]),
    ],
)
def test_real_jsonpath_slices_document_list_not_document_values(
    content: str, query: str, selected: list[object], many: bool
) -> None:
    _require_engine()
    assert _load_stream(content, query, many=many) == ([selected] if many else selected)


def test_real_jsonpath_slices_each_file_stream_without_slicing_file_collection() -> None:
    _require_engine()
    root = MemoryRoot("stream-files")
    assert load_yaml_compose(
        StringIO("!include {sources: [a.yaml, b.yaml, empty.yaml], documents: all, json_path: '$[1:]'}"),
        allowed_roots=[root],
        include_resolver=MemoryIncludeResolver(
            root,
            {
                "a.yaml": "x: 0\n---\nx: 1\n---\nx: 2\n",
                "b.yaml": "x: 3\n",
                "empty.yaml": "",
            },
        ),
    ) == [[{"x": 1}, {"x": 2}], [], []]


@pytest.mark.parametrize(
    ("query", "selected"),
    [
        ("$[1:]", [[{"x": 3}], []]),
        ("$[0][1:]", [{"x": 1}, {"x": 2}]),
        ("$[0]", [[{"x": 0}, {"x": 1}, {"x": 2}]]),
    ],
)
def test_real_jsonpath_can_select_explicitly_from_outer_file_collection(query: str, selected: list[object]) -> None:
    _require_engine()
    root = MemoryRoot("outer-selection")
    assert (
        load_yaml_compose(
            StringIO("!include {path: aggregate.yaml, json_path: " + json.dumps(query) + "}"),
            allowed_roots=[root],
            include_resolver=MemoryIncludeResolver(
                root,
                {
                    "aggregate.yaml": "!include {sources: [a.yaml, b.yaml, empty.yaml], documents: all}",
                    "a.yaml": "x: 0\n---\nx: 1\n---\nx: 2\n",
                    "b.yaml": "x: 3\n",
                    "empty.yaml": "",
                },
            ),
        )
        == selected
    )


@pytest.mark.parametrize("many", [False, True])
def test_stream_slice_does_not_hide_late_parse_errors(monkeypatch: pytest.MonkeyPatch, many: bool) -> None:
    def select(value: object, query: str, limits: JsonPathLimits) -> list[object]:
        raise AssertionError("selection must follow complete stream validation")

    monkeypatch.setattr("yaml_compose.loader.select_jsonpath", select)
    with pytest.raises(yaml.YAMLError):
        _load_stream("x: 0\n---\nx: 1\n---\nbroken: [\n", "$[:1]", many=many)


@pytest.mark.parametrize("many", [False, True])
def test_stream_slice_charges_complete_selection_input(many: bool) -> None:
    root = MemoryRoot("stream-budget")
    options = (
        "!include {sources: [bundle.yaml], documents: all" if many else "!include {documents: all, path: bundle.yaml"
    )
    with pytest.raises(yaml.YAMLError, match="maximum input nodes"):
        load_yaml_compose(
            StringIO(options + ", json_path: '$[:1]'}"),
            allowed_roots=[root],
            include_resolver=MemoryIncludeResolver(root, {"bundle.yaml": "x: 0\n---\nx: 1\n"}),
            jsonpath_limits=JsonPathLimits(max_input_nodes=3),
        )
