"""Nested include failures retain their ordered source route."""

from __future__ import annotations

from io import StringIO
from pathlib import Path, PurePosixPath

import pytest
import yaml
from yaml.constructor import ConstructorError
from yaml.parser import ParserError

from yaml_compose import (
    IncludeRouteFrame,
    MemoryIncludeResolver,
    MemoryRoot,
    MemorySource,
    YamlConfigLoader,
    get_include_route,
    load_yaml_file,
)


def _write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def _route(error: BaseException) -> tuple[IncludeRouteFrame, ...]:
    route = get_include_route(error)
    assert route
    assert all(isinstance(frame, IncludeRouteFrame) for frame in route)
    return route


def test_get_include_route_returns_empty_for_unrelated_errors() -> None:
    assert get_include_route(ValueError("ordinary failure")) == ()


def test_single_level_include_failure_keeps_existing_constructor_error_shape(test_path: Path) -> None:
    main = test_path / "main.yaml"
    _write(main, "%LOADER YamlCompose\n---\nvalue: !include broken.json\n")
    _write(test_path / "broken.json", b'{"value": }')

    with pytest.raises(ConstructorError, match="invalid JSON include source") as caught:
        load_yaml_file(main)

    error = caught.value
    assert error.problem_mark is not None and error.problem_mark.name == str(main)
    assert isinstance(error.__cause__, Exception)
    route = _route(error)
    assert len(route) == 1
    assert (route[0].tag, route[0].requested_path, route[0].resolved_source) == (
        "!include",
        "broken.json",
        str(test_path / "broken.json"),
    )


def test_nested_yaml_parse_error_keeps_leaf_mark_and_ordered_filesystem_route(test_path: Path) -> None:
    main = test_path / "main.yaml"
    env = test_path / "env.yaml"
    broken = test_path / "broken.yaml"
    _write(main, "%LOADER YamlCompose\n---\nvalue: !include env.yaml\n")
    _write(env, "value: !include broken.yaml\n")
    _write(broken, "value: [unfinished\n")

    with pytest.raises(ParserError) as caught:
        load_yaml_file(main)

    error = caught.value
    assert error.problem_mark is not None
    assert error.problem_mark.name == str(broken)
    route = _route(error)
    assert [(frame.including_source, frame.requested_path, frame.resolved_source) for frame in route] == [
        (str(main), "env.yaml", str(env)),
        (str(env), "broken.yaml", str(broken)),
    ]
    assert (route[0].line, route[0].column, route[0].tag) == (3, 8, "!include")
    rendered_route = str(error).split("Include route (outermost first):", maxsplit=1)[1]
    assert str(error).count("Include route (outermost first):") == 1
    assert rendered_route.count(f"  {main}:3:8 !include") == 1
    assert rendered_route.count(f"  {env}:1:8 !include") == 1


def test_nested_json_and_late_jsonl_errors_preserve_constructor_errors_and_causes(test_path: Path) -> None:
    main = test_path / "main.yaml"
    env = test_path / "env.yaml"
    _write(main, "%LOADER YamlCompose\n---\npayload: !include env.yaml\n")
    _write(env, "payload: !include broken.json\n")
    _write(test_path / "broken.json", b'{"first": 1, "second": }')

    with pytest.raises(ConstructorError, match="invalid JSON include source") as caught:
        load_yaml_file(main)

    error = caught.value
    assert isinstance(error.__cause__, Exception)
    assert error.problem_mark is not None and error.problem_mark.name == str(env)
    assert [frame.requested_path for frame in _route(error)] == ["env.yaml", "broken.json"]
    assert str(test_path / "broken.json") in str(error)

    _write(env, "payload: !include broken.jsonl\n")
    _write(test_path / "broken.jsonl", b'{"ok": 1}\nnot-json\n')
    with pytest.raises(ConstructorError, match="invalid JSONL include source") as late_record:
        load_yaml_file(main)
    assert [frame.requested_path for frame in _route(late_record.value)] == ["env.yaml", "broken.jsonl"]
    assert late_record.value.__cause__ is not None

    _write(env, "payload: !include invalid.json\n")
    _write(test_path / "invalid.json", b'{"value": "\xff"}')
    with pytest.raises(ConstructorError, match="not UTF-8 text") as invalid_utf8:
        load_yaml_file(main)
    assert [frame.requested_path for frame in _route(invalid_utf8.value)] == ["env.yaml", "invalid.json"]
    assert invalid_utf8.value.__cause__ is not None


def test_nested_missing_and_invalid_text_errors_have_route_and_preserve_cause(test_path: Path) -> None:
    root = MemoryRoot("route-missing")
    resolver = MemoryIncludeResolver(root, {"env.yaml": "payload: !include absent.json\n"})
    loader = YamlConfigLoader(include_resolver=resolver, search_paths=[root], allowed_roots=[root])

    with pytest.raises(yaml.YAMLError, match="was not found") as missing:
        loader.load_include(
            StringIO("payload: !include env.yaml\n"),
            source=MemorySource(root, PurePosixPath("main.yaml")),
        )

    route = _route(missing.value)
    assert [frame.including_source for frame in route] == [
        "memory:route-missing/main.yaml",
        "memory:route-missing/env.yaml",
    ]
    assert route[1].requested_path == "absent.json"
    assert route[1].resolved_source is None
    assert missing.value.__cause__ is not None

    _write(test_path / "main.yaml", "%LOADER YamlCompose\n---\npayload: !include env.yaml\n")
    _write(test_path / "env.yaml", "payload: !include-text bytes.txt\n")
    _write(test_path / "bytes.txt", b"\xff")
    with pytest.raises(ConstructorError, match="is not UTF-8 text") as invalid_text:
        load_yaml_file(test_path / "main.yaml")
    assert [frame.requested_path for frame in _route(invalid_text.value)] == ["env.yaml", "bytes.txt"]
    assert isinstance(invalid_text.value.__cause__, UnicodeDecodeError)


@pytest.mark.parametrize(
    ("inner", "depth_limited", "message", "resolved_suffix"),
    [
        ("payload: !include value.yaml\n", True, "maximum include depth", "value.yaml"),
        ("payload: !include ../outside.json\n", False, "outside the allowed roots", "outside.json"),
    ],
)
def test_nested_limit_and_confinement_failures_report_each_include_site(
    test_path: Path,
    inner: str,
    depth_limited: bool,
    message: str,
    resolved_suffix: str,
) -> None:
    allowed = test_path / "allowed"
    main = allowed / "main.yaml"
    env = allowed / "env.yaml"
    _write(main, "%LOADER YamlCompose\n---\npayload: !include env.yaml\n")
    _write(env, inner)
    _write(allowed / "value.yaml", "value: okay\n")
    _write(test_path / "outside.json", b'{"value": 1}')

    with pytest.raises(yaml.YAMLError, match=message) as caught:
        if depth_limited:
            load_yaml_file(main, allowed_roots=[allowed], max_include_depth=1)
        else:
            load_yaml_file(main, allowed_roots=[allowed])

    route = _route(caught.value)
    expected_requested = "value.yaml" if resolved_suffix == "value.yaml" else "../outside.json"
    assert [frame.requested_path for frame in route] == ["env.yaml", expected_requested]
    assert len(route) == 2
    if depth_limited:
        assert route[-1].resolved_source is not None and route[-1].resolved_source.endswith(resolved_suffix)
    else:
        # The filesystem resolver rejects the escaping path before it returns a source identity.
        assert route[-1].resolved_source is None


@pytest.mark.parametrize(
    ("limit_name", "limit_value", "message"),
    [
        ("max_include_files", 1, "maximum include count"),
        ("max_file_bytes", 19, "per-file size limit"),
        ("max_total_include_bytes", 19, "maximum total included bytes"),
    ],
)
def test_nested_file_and_byte_budget_failures_include_the_child_source_route(
    test_path: Path,
    limit_name: str,
    limit_value: int,
    message: str,
) -> None:
    main = test_path / "main.yaml"
    _write(main, "%LOADER YamlCompose\n---\nvalue: !include env.yaml\n")
    env_content = b"x: !include v.yaml\n"
    assert len(env_content) == 19
    _write(test_path / "env.yaml", env_content)
    _write(test_path / "v.yaml", f"value: {'x' * 64}\n")

    with pytest.raises(yaml.YAMLError, match=message) as caught:
        if limit_name == "max_include_files":
            load_yaml_file(main, max_include_files=limit_value)
        elif limit_name == "max_file_bytes":
            load_yaml_file(main, max_file_bytes=limit_value)
        else:
            assert limit_name == "max_total_include_bytes"
            load_yaml_file(main, max_total_include_bytes=limit_value)

    route = _route(caught.value)
    assert [frame.requested_path for frame in route] == ["env.yaml", "v.yaml"]
    assert route[-1].resolved_source == str((test_path / "v.yaml").resolve())


@pytest.mark.parametrize(
    ("merge_source", "expression", "message"),
    [
        ("scalar.yaml", "<<<: !include scalar.yaml\n", "each source for <<< must be a mapping"),
        ("scalar.yaml", "items: [!spread [!include scalar.yaml]]\n", "source must be a sequence"),
    ],
)
def test_overlay_shape_errors_include_the_source_route(
    test_path: Path,
    merge_source: str,
    expression: str,
    message: str,
) -> None:
    main = test_path / "main.yaml"
    env = test_path / "env.yaml"
    _write(main, "%LOADER YamlCompose\n---\nvalue: !include env.yaml\n")
    _write(env, expression)
    _write(test_path / merge_source, "scalar value\n")

    with pytest.raises(yaml.YAMLError, match=message) as caught:
        load_yaml_file(main)

    route = _route(caught.value)
    assert [frame.requested_path for frame in route] == ["env.yaml", "scalar.yaml"]
    assert route[-1].resolved_source == str((test_path / merge_source).resolve())


def test_spread_marker_in_mapping_reports_its_included_sequence_source(test_path: Path) -> None:
    main = test_path / "main.yaml"
    _write(main, "%LOADER YamlCompose\n---\nvalue: !include env.yaml\n")
    _write(test_path / "env.yaml", "value: !spread [!include items.yaml]\n")
    _write(test_path / "items.yaml", "- one\n")

    with pytest.raises(yaml.YAMLError, match="!spread is only valid as a list item") as caught:
        load_yaml_file(main)

    route = _route(caught.value)
    assert [frame.requested_path for frame in route] == ["env.yaml", "items.yaml"]


def test_spread_used_as_mapping_key_reports_its_included_sequence_source(test_path: Path) -> None:
    main = test_path / "main.yaml"
    _write(main, "%LOADER YamlCompose\n---\nvalue: !include env.yaml\n")
    _write(test_path / "env.yaml", "!spread [!include items.yaml]: invalid-key\n")
    _write(test_path / "items.yaml", "- one\n")

    with pytest.raises(yaml.YAMLError, match="cannot be used as mapping keys") as caught:
        load_yaml_file(main)

    assert [frame.requested_path for frame in _route(caught.value)] == ["env.yaml", "items.yaml"]


@pytest.mark.parametrize(
    ("mapping", "message", "source_names"),
    [
        ("? !include key.json\n: value\n", "found unhashable key", ["key.json"]),
        (
            "? !include first.json\n: first\n? !include second.json\n: second\n",
            "found duplicate key",
            ["second.json"],
        ),
    ],
)
def test_included_mapping_key_failures_report_the_key_source_route(
    test_path: Path,
    mapping: str,
    message: str,
    source_names: list[str],
) -> None:
    main = test_path / "main.yaml"
    _write(main, f"%LOADER YamlCompose\n---\n{mapping}")
    _write(test_path / "key.json", b"[]")
    _write(test_path / "first.json", b'"same"')
    _write(test_path / "second.json", b'"same"')

    with pytest.raises(ConstructorError, match=message) as caught:
        load_yaml_file(main)

    route = _route(caught.value)
    assert [frame.requested_path for frame in route] == source_names
    assert route[0].including_source == str(main)


def test_cycle_route_is_ordered_once_and_reused_loader_has_fresh_route(test_path: Path) -> None:
    main = test_path / "main.yaml"
    env = test_path / "env.yaml"
    _write(main, "%LOADER YamlCompose\n---\nvalue: !include env.yaml\n")
    _write(env, "value: !include main.yaml\n")
    loader = YamlConfigLoader()

    with pytest.raises(yaml.YAMLError, match="include cycle detected") as cycle:
        loader.load_file(main)
    assert [frame.requested_path for frame in _route(cycle.value)] == ["env.yaml", "main.yaml"]
    assert str(cycle.value).count("Include route (outermost first):") == 1

    _write(main, "%LOADER YamlCompose\n---\nvalue: !include env.yaml\n")
    _write(env, "value: !include missing.yaml\n")
    with pytest.raises(yaml.YAMLError, match="was not found") as missing:
        loader.load_file(main)
    assert [frame.requested_path for frame in _route(missing.value)] == ["env.yaml", "missing.yaml"]


def test_failed_second_sibling_does_not_report_first_sibling_route(test_path: Path) -> None:
    main = test_path / "main.yaml"
    env = test_path / "env.yaml"
    _write(main, "%LOADER YamlCompose\n---\nvalue: !include env.yaml\n")
    _write(env, "first: !include okay.yaml\nsecond: !include broken.json\n")
    _write(test_path / "okay.yaml", "ready: true\n")
    _write(test_path / "broken.json", b'{"broken": }')

    with pytest.raises(ConstructorError, match="invalid JSON include source") as caught:
        load_yaml_file(main)

    route = _route(caught.value)
    assert [frame.requested_path for frame in route] == ["env.yaml", "broken.json"]
    assert "okay.yaml" not in str(caught.value)
