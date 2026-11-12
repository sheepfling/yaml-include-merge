from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
import yaml

from yaml_compose import (
    DEFAULT_JSONPATH_LIMITS,
    JSONPATH_TAG,
    JsonPathLimits,
    MemoryIncludeResolver,
    MemoryRoot,
    YamlConfigLoader,
    get_include_route,
    load_yaml_compose,
    load_yaml_dispatch,
    load_yaml_file,
)


def capture_selection(monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, str, JsonPathLimits]]:
    calls: list[tuple[object, str, JsonPathLimits]] = []

    def select(value: object, query: str, limits: JsonPathLimits) -> list[object]:
        calls.append((value, query, limits))
        return [value]

    monkeypatch.setattr("yaml_compose.loader.select_jsonpath", select)
    return calls


def test_tag_constructs_source_before_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = capture_selection(monkeypatch)
    root = MemoryRoot("jsonpath")
    resolver = MemoryIncludeResolver(root, {"base.yaml": "defaults: {port: 80, host: localhost}\n"})
    result = load_yaml_compose(
        StringIO("!jsonpath [{<<<: !include {path: 'base.yaml', json_pointer: '/defaults'}, port: 8080}, '$']\n"),
        include_resolver=resolver,
        allowed_roots=[root],
    )
    expected = {"port": 8080, "host": "localhost"}
    assert result == [expected]
    assert calls == [(expected, "$", DEFAULT_JSONPATH_LIMITS)]
    assert JSONPATH_TAG == "!jsonpath"


@pytest.mark.parametrize(
    ("source", "files", "expected"),
    [
        ("!include {path: 'data.json', json_pointer: '/items'}", {"data.json": '{"items": [1, 2]}'}, [1, 2]),
        ("!include {documents: all, path: data.yaml}", {"data.yaml": "---\na: 1\n---\na: 2\n"}, [{"a": 1}, {"a": 2}]),
        ("!include-table data.csv", {"data.csv": "a,b\n1,2\n"}, [["a", "b"], ["1", "2"]]),
        ("!include-text data.txt", {"data.txt": "123"}, "123"),
    ],
)
def test_tag_composes_with_existing_includes(
    monkeypatch: pytest.MonkeyPatch, source: str, files: dict[str, str], expected: object
) -> None:
    calls = capture_selection(monkeypatch)
    root = MemoryRoot("sources")
    assert load_yaml_compose(
        StringIO(f"!jsonpath [{source}, '$']\n"),
        allowed_roots=[root],
        include_resolver=MemoryIncludeResolver(root, files),
    ) == [expected]
    assert calls[0][0] == expected


@pytest.mark.parametrize("text", ["!jsonpath value", "!jsonpath []", "!jsonpath [one]", "!jsonpath [one, two, three]"])
def test_invalid_tag_shape(text: str) -> None:
    with pytest.raises(yaml.YAMLError, match=r"expects \[source, query\]"):
        load_yaml_compose(StringIO(text))


@pytest.mark.parametrize("query", ["null", "true", "42", "[]", "{}", "!include query.txt"])
def test_query_must_be_literal_string(query: str) -> None:
    with pytest.raises(yaml.YAMLError, match="query must be a string"):
        load_yaml_compose(StringIO(f"!jsonpath [{{}}, {query}]"))


def test_jsonpath_is_not_uri_decoded(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = capture_selection(monkeypatch)
    query = "$['space%20key#/~']"
    load_yaml_compose(StringIO(f'!jsonpath [{{}}, "{query}"]'))
    assert calls[0][1] == query


def test_errors_keep_query_mark_and_include_route(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(value: object, query: str, limits: JsonPathLimits) -> list[object]:
        raise ValueError("test engine failure")

    monkeypatch.setattr("yaml_compose.loader.select_jsonpath", fail)
    root = MemoryRoot("routes")
    with pytest.raises(yaml.YAMLError, match="cannot select JSONPath") as caught:
        load_yaml_compose(
            StringIO("!include outer.yaml\n"),
            allowed_roots=[root],
            include_resolver=MemoryIncludeResolver(
                root, {"outer.yaml": "!jsonpath [!include inner.json, '$.items']\n", "inner.json": "{}"}
            ),
        )
    route = get_include_route(caught.value)
    assert [frame.requested_path for frame in route] == ["outer.yaml", "inner.json"]
    assert "$.items" in str(caught.value)
    assert "test engine failure" in str(caught.value)
    assert "outer.yaml" in str(caught.value)


def test_jsonpath_list_is_not_an_implicit_mapping_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = capture_selection(monkeypatch)
    root = MemoryRoot("merge")
    with pytest.raises(yaml.YAMLError, match="must be a mapping") as caught:
        load_yaml_compose(
            StringIO("<<<: !jsonpath [!include data.json, '$']\n"),
            allowed_roots=[root],
            include_resolver=MemoryIncludeResolver(root, {"data.json": '{"a":1}'}),
        )
    assert len(calls) == 1
    assert get_include_route(caught.value)[-1].requested_path == "data.json"


def test_jsonpath_spreads_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    capture_selection(monkeypatch)
    assert load_yaml_compose(StringIO("[before, !spread [!jsonpath [{a: 1}, '$']], after]")) == [
        "before",
        {"a": 1},
        "after",
    ]


def test_policy_passes_through_all_entrypoints_and_nested_includes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = capture_selection(monkeypatch)
    limits = JsonPathLimits(max_matches=3)
    document = "%LOADER YamlCompose\n---\n!jsonpath [{a: 1}, '$']\n"
    path = tmp_path / "config.yaml"
    path.write_text(document, encoding="utf-8")
    load_yaml_dispatch(StringIO(document), jsonpath_limits=limits)
    load_yaml_compose(StringIO(document), jsonpath_limits=limits)
    load_yaml_file(path, jsonpath_limits=limits)
    loader = YamlConfigLoader(jsonpath_limits=limits, allowed_roots=[tmp_path], search_paths=[tmp_path])
    assert loader.jsonpath_limits is limits
    loader.load_stream(StringIO(document))
    loader.load_include(StringIO(document))
    loader.load_file(path)
    loader.load_include(StringIO("!include config.yaml\n"))
    assert len(calls) == 7
    assert all(call[2] is limits for call in calls)


def test_configured_loader_registers_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    capture_selection(monkeypatch)

    class CustomLoader(yaml.SafeLoader):
        pass

    assert load_yaml_compose(StringIO("!jsonpath [{a: 1}, '$']"), yaml_loader=CustomLoader) == [{"a": 1}]


def test_ordinary_loader_does_not_register_tag() -> None:
    with pytest.raises(yaml.YAMLError, match="could not determine a constructor"):
        load_yaml_dispatch(StringIO("!jsonpath [{a: 1}, '$']"))


def test_include_validation_happens_before_jsonpath(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = capture_selection(monkeypatch)
    root = MemoryRoot("validation")
    with pytest.raises(yaml.YAMLError):
        load_yaml_compose(
            StringIO("!jsonpath [!include {documents: all, path: data.yaml}, '$[0]']"),
            allowed_roots=[root],
            include_resolver=MemoryIncludeResolver(root, {"data.yaml": "---\na: 1\n---\nbroken: ["}),
        )
    assert calls == []
