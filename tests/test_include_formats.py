"""JSON and TOML includes retain the shared resolver and composition behavior."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timezone
from io import BytesIO, StringIO
from pathlib import Path, PurePosixPath
from typing import Literal, cast

import pytest
import yaml
from yaml.constructor import ConstructorError

from yaml_compose import (
    FileSource,
    IncludeLimits,
    IncludeRequest,
    MemoryIncludeResolver,
    MemoryRoot,
    MemorySource,
    ResolvedInclude,
    YamlConfigLoader,
    load_yaml_file,
)


def test_json_mapping_include_deep_merges_and_local_values_win(test_path: Path) -> None:
    base = test_path / "base.json"
    base.write_text(
        '{"service":{"host":"shared","port":8080,"labels":["base"]},"enabled":true}',
        encoding="utf-8",
    )
    config = test_path / "main.yaml"
    config.write_text(
        "%LOADER YamlCompose\n---\n<<<: !include ./base.json\nservice:\n  host: production\n  labels: [production]\n",
        encoding="utf-8",
    )

    assert load_yaml_file(config) == {
        "service": {"host": "production", "port": 8080, "labels": ["production"]},
        "enabled": True,
    }


def test_toml_mapping_include_preserves_native_date_time_values(test_path: Path) -> None:
    base = test_path / "base.toml"
    base.write_text(
        """host = "db.example.test"
port = 5432
enabled = true
timeout = 1.5
local_date = 1979-05-27
local_time = 07:32:00
timestamp = 1979-05-27T07:32:00Z
""",
        encoding="utf-8",
    )
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\n<<<: !include base.toml\nport: 6432\n", encoding="utf-8")

    result = load_yaml_file(config)

    assert result == {
        "host": "db.example.test",
        "port": 6432,
        "enabled": True,
        "timeout": 1.5,
        "local_date": date(1979, 5, 27),
        "local_time": time(7, 32),
        "timestamp": datetime(1979, 5, 27, 7, 32, tzinfo=timezone.utc),
    }


def test_toml_arrays_remain_lists_inside_the_included_mapping(test_path: Path) -> None:
    source = test_path / "settings.toml"
    source.write_text(
        'steps = ["lint", "test"]\n\n[[jobs]]\nname = "build"\n\n[[jobs]]\nname = "publish"\n',
        encoding="utf-8",
    )
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nsettings: !include settings.toml\n", encoding="utf-8")

    assert load_yaml_file(config) == {
        "settings": {"steps": ["lint", "test"], "jobs": [{"name": "build"}, {"name": "publish"}]}
    }


def test_toml_nonfinite_floats_follow_toml_parser_semantics(test_path: Path) -> None:
    source = test_path / "settings.toml"
    source.write_text("positive_infinity = inf\nnot_a_number = nan\n", encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nsettings: !include settings.toml\n", encoding="utf-8")

    result = load_yaml_file(config)

    assert isinstance(result, dict)
    values = cast(dict[str, object], result["settings"])
    positive_infinity = values["positive_infinity"]
    not_a_number = values["not_a_number"]
    assert math.isinf(cast(float, positive_infinity))
    assert math.isnan(cast(float, not_a_number))


def test_json_arrays_can_be_included_or_spread_in_order(test_path: Path) -> None:
    items = test_path / "items.json"
    items.write_text('["lint","test","test"]', encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text(
        "%LOADER YamlCompose\n---\nwhole: !include items.json\n"
        "steps: [prepare, !spread [!include items.json], deploy]\n",
        encoding="utf-8",
    )

    assert load_yaml_file(config) == {
        "whole": ["lint", "test", "test"],
        "steps": ["prepare", "lint", "test", "test", "deploy"],
    }


def test_json_scalars_and_null_are_native_values(test_path: Path) -> None:
    scalar = test_path / "scalar.json"
    scalar.write_text('"café"', encoding="utf-8")
    integer = test_path / "integer.json"
    integer.write_text("42", encoding="utf-8")
    decimal = test_path / "decimal.json"
    decimal.write_text("1.25", encoding="utf-8")
    boolean = test_path / "boolean.json"
    boolean.write_text("true", encoding="utf-8")
    null = test_path / "null.json"
    null.write_text("null", encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text(
        "%LOADER YamlCompose\n---\ntext: !include scalar.json\n"
        "integer: !include integer.json\ndecimal: !include decimal.json\n"
        "boolean: !include boolean.json\nvalue: !include null.json\n",
        encoding="utf-8",
    )

    assert load_yaml_file(config) == {
        "text": "café",
        "integer": 42,
        "decimal": 1.25,
        "boolean": True,
        "value": None,
    }


def test_json_dispatch_still_works_when_nested_yaml_dispatch_is_disabled(test_path: Path) -> None:
    (test_path / "base.json").write_text('{"value": 123}', encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nbase: !include base.json\n", encoding="utf-8")

    assert load_yaml_file(config, recursive_dispatch=False) == {"base": {"value": 123}}


@pytest.mark.parametrize(
    ("suffix", "content", "expected"),
    [
        (".json", b'{"value":"json"}', {"value": "json"}),
        (".toml", b'value = "toml"\n', {"value": "toml"}),
    ],
)
def test_custom_resolver_dispatches_by_resolved_source_suffix(
    test_path: Path, suffix: str, content: bytes, expected: dict[str, str]
) -> None:
    actual = test_path / f"actual{suffix}"
    actual.write_bytes(content)
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\npayload: !include alias.yaml\n", encoding="utf-8")

    class RedirectResolver:
        def __init__(self) -> None:
            self.requests: list[IncludeRequest] = []

        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            self.requests.append(request)
            return ResolvedInclude(FileSource(actual), lambda: actual.open("rb"))

    resolver = RedirectResolver()

    assert load_yaml_file(config, include_resolver=resolver) == {"payload": expected}
    assert len(resolver.requests) == 1
    assert resolver.requests[0].name == "alias.yaml"


def test_redirected_source_still_obeys_allowed_roots_before_opening(test_path: Path) -> None:
    allowed = test_path / "allowed"
    allowed.mkdir()
    outside = test_path / "outside.json"
    outside.write_text('{"value":1}', encoding="utf-8")
    config = allowed / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include alias.yaml\n", encoding="utf-8")
    opened: list[bool] = []

    class RedirectResolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            return ResolvedInclude(FileSource(outside), lambda: (opened.append(True), outside.open("rb"))[1])

    with pytest.raises(yaml.YAMLError, match="outside the allowed roots"):
        load_yaml_file(config, allowed_roots=[allowed], include_resolver=RedirectResolver())

    assert opened == []


def test_redirected_source_still_obeys_file_byte_budget(test_path: Path) -> None:
    actual = test_path / "actual.json"
    actual.write_text('{"value":"too large"}', encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include alias.yaml\n", encoding="utf-8")

    class RedirectResolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            return ResolvedInclude(FileSource(actual), lambda: actual.open("rb"))

    with pytest.raises(yaml.YAMLError, match="per-file size limit"):
        load_yaml_file(config, include_resolver=RedirectResolver(), max_file_bytes=4)


def test_other_include_suffixes_keep_legacy_yaml_parsing(test_path: Path) -> None:
    (test_path / "legacy.conf").write_text("value: 123\n", encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nlegacy: !include legacy.conf\n", encoding="utf-8")

    assert load_yaml_file(config) == {"legacy": {"value": 123}}


def test_yaml_overlay_can_replace_clear_null_or_delete_json_values(test_path: Path) -> None:
    base = test_path / "base.json"
    base.write_text(
        '{"service":{"labels":["old"],"clearable":[1],"nullable":"old","remove":true}}',
        encoding="utf-8",
    )
    config = test_path / "main.yaml"
    config.write_text(
        "%LOADER YamlCompose\n---\n<<<: !include base.json\n"
        "service:\n  labels: [new]\n  clearable: []\n  nullable: null\n  remove: !delete\n",
        encoding="utf-8",
    )

    assert load_yaml_file(config) == {"service": {"labels": ["new"], "clearable": [], "nullable": None}}


def test_mixed_json_and_toml_sources_keep_merge_source_precedence(test_path: Path) -> None:
    (test_path / "first.json").write_text('{"source":"first","service":{"host":"first","port":8080}}', encoding="utf-8")
    (test_path / "second.toml").write_text(
        'source = "second"\n[service]\nhost = "second"\nport = 9000\n', encoding="utf-8"
    )
    config = test_path / "main.yaml"
    config.write_text(
        "%LOADER YamlCompose\n---\n<<<: [!include first.json, !include second.toml]\nservice: !replace {port: 443}\n",
        encoding="utf-8",
    )

    assert load_yaml_file(config) == {"source": "first", "service": {"port": 443}}


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ('{"broken":', "Expecting value"),
        ('{"key": 1, "key": 2}', "duplicate JSON object key 'key'"),
        ('{"value": NaN}', "non-finite JSON constant 'NaN'"),
        ('{"value": Infinity}', "non-finite JSON constant 'Infinity'"),
        ('{"value": -Infinity}', "non-finite JSON constant '-Infinity'"),
        ('{"value": 1e999}', "non-finite JSON number '1e999'"),
    ],
)
def test_json_errors_include_the_source_and_yaml_include_context(test_path: Path, contents: str, message: str) -> None:
    broken = test_path / "broken.json"
    broken.write_text(contents, encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include broken.json\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError, match="invalid JSON include source") as caught:
        load_yaml_file(config)

    assert str(broken) in str(caught.value)
    assert message in str(caught.value)
    assert caught.value.__cause__ is not None


@pytest.mark.parametrize(
    "contents",
    [
        "value = 1\nvalue = 2\n",
        "value = [unterminated\n",
    ],
)
def test_toml_errors_include_the_source_and_yaml_include_context(test_path: Path, contents: str) -> None:
    broken = test_path / "broken.toml"
    broken.write_text(contents, encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include broken.toml\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError, match="invalid TOML include source") as caught:
        load_yaml_file(config)

    assert str(broken) in str(caught.value)
    assert caught.value.__cause__ is not None


def test_malformed_utf8_is_reported_before_format_parsing(test_path: Path) -> None:
    broken = test_path / "broken.json"
    broken.write_bytes(b'{"value":"\xff"}')
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include broken.json\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError, match="is not UTF-8 text"):
        load_yaml_file(config)


@pytest.mark.parametrize(
    ("name", "content", "error"),
    [
        ("broken.json", b'{"broken":', "invalid JSON include source"),
        ("broken.toml", b"broken = [", "invalid TOML include source"),
        ("invalid-encoding.toml", b"value = \xff", "is not UTF-8 text"),
    ],
)
def test_custom_resolver_stream_closes_on_format_and_encoding_errors(name: str, content: bytes, error: str) -> None:
    root = MemoryRoot("stream-cleanup-fixture")
    source = MemorySource(root, PurePosixPath(name))

    class TrackingResolver:
        def __init__(self) -> None:
            self.stream = BytesIO(content)

        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            return ResolvedInclude(source, lambda: self.stream)

    resolver = TrackingResolver()
    loader = YamlConfigLoader(include_resolver=resolver, search_paths=[root], allowed_roots=[root])

    with pytest.raises(yaml.YAMLError, match=error):
        loader.load_include(
            StringIO(f"value: !include {name}\n"),
            source=MemorySource(root, PurePosixPath("main.yaml")),
        )

    assert resolver.stream.closed


@pytest.mark.parametrize(
    ("suffix", "content", "format_name"),
    [
        (".json", b'\xef\xbb\xbf{"value":1}', "JSON"),
        (".toml", b"\xef\xbb\xbfvalue = 1\n", "TOML"),
    ],
)
def test_utf8_bom_is_rejected_as_a_marked_include_parse_error(
    test_path: Path, suffix: str, content: bytes, format_name: str
) -> None:
    source = test_path / f"bom{suffix}"
    source.write_bytes(content)
    config = test_path / "main.yaml"
    config.write_text(f"%LOADER YamlCompose\n---\nvalue: !include {source.name}\n", encoding="utf-8")

    with pytest.raises(ConstructorError, match=f"invalid {format_name} include source") as caught:
        load_yaml_file(config)

    assert str(source) in str(caught.value)
    assert caught.value.__cause__ is not None
    assert caught.value.problem_mark is not None
    assert caught.value.problem_mark.name == str(config)


def test_nested_relative_yaml_includes_can_compose_json_and_toml(test_path: Path) -> None:
    layers = test_path / "layers"
    layers.mkdir()
    (test_path / "defaults.json").write_text(
        '{"service":{"host":"default","labels":["base"],"token":"remove-me"}}',
        encoding="utf-8",
    )
    (test_path / "database.toml").write_text('[database]\nhost = "db.example.test"\nport = 5432\n', encoding="utf-8")
    (layers / "region.yaml").write_text(
        "<<<: !include ../defaults.json\ndatabase: !include ../database.toml\n"
        "service:\n  host: regional\n  labels: []\n",
        encoding="utf-8",
    )
    config = test_path / "main.yaml"
    config.write_text(
        "%LOADER YamlCompose\n---\n<<<: !include ./layers/region.yaml\n"
        "service:\n  host: production\n  token: !delete\n",
        encoding="utf-8",
    )

    assert load_yaml_file(config) == {
        "service": {"host": "production", "labels": []},
        "database": {"database": {"host": "db.example.test", "port": 5432}},
    }


def test_json_includes_obey_explicit_allowed_roots(test_path: Path) -> None:
    allowed = test_path / "allowed"
    allowed.mkdir()
    (test_path / "outside.json").write_text('{"value": 1}', encoding="utf-8")
    config = allowed / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include ../outside.json\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError, match="allowed roots"):
        load_yaml_file(config, allowed_roots=[allowed])


@pytest.mark.parametrize(
    ("limit_name", "limit", "message"),
    [
        ("max_include_depth", 0, "maximum include depth"),
        ("max_include_files", 0, "maximum include count"),
        ("max_file_bytes", 1, "per-file size limit"),
        ("max_total_include_bytes", 1, "maximum total included bytes"),
    ],
)
def test_json_include_uses_shared_file_and_depth_budgets(
    test_path: Path,
    limit_name: Literal["max_include_depth", "max_include_files", "max_file_bytes", "max_total_include_bytes"],
    limit: int,
    message: str,
) -> None:
    (test_path / "base.json").write_text('{"value": 123}', encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include base.json\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError, match=message):
        if limit_name == "max_include_depth":
            load_yaml_file(config, max_include_depth=limit)
        elif limit_name == "max_include_files":
            load_yaml_file(config, max_include_files=limit)
        elif limit_name == "max_file_bytes":
            load_yaml_file(config, max_file_bytes=limit)
        else:
            load_yaml_file(config, max_total_include_bytes=limit)


def test_json_include_counts_toward_configured_file_limit(test_path: Path) -> None:
    (test_path / "base.json").write_text('{"value": 123}', encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include base.json\n", encoding="utf-8")
    limits = IncludeLimits(max_include_files=0)

    with pytest.raises(yaml.YAMLError, match="maximum include count"):
        load_yaml_file(config, include_limits=limits)


@pytest.mark.parametrize(
    ("policy", "expected"),
    [("local-first", "local"), ("search-first", "shared")],
)
def test_json_include_uses_existing_search_precedence(
    test_path: Path, policy: Literal["local-first", "search-first"], expected: str
) -> None:
    local = test_path / "app"
    shared = test_path / "shared"
    local.mkdir()
    shared.mkdir()
    (local / "value.json").write_text('{"source":"local"}', encoding="utf-8")
    (shared / "value.json").write_text('{"source":"shared"}', encoding="utf-8")
    config = local / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include value.json\n", encoding="utf-8")

    assert load_yaml_file(config, search_paths=[shared], include_search=policy) == {"value": {"source": expected}}


def test_non_mapping_json_include_is_rejected_as_an_overlay_source(test_path: Path) -> None:
    (test_path / "sequence.json").write_text('["value"]', encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\n<<<: !include sequence.json\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError, match="each source for <<< must be a mapping"):
        load_yaml_file(config)


def test_yaml_cycle_detection_remains_active_alongside_format_dispatch(test_path: Path) -> None:
    config = test_path / "cycle.yaml"
    config.write_text("%LOADER YamlCompose\n---\nchild: !include ./cycle.yaml\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError, match="include cycle detected"):
        load_yaml_file(config)


def test_memory_resolver_dispatches_nested_yaml_to_json_by_source_suffix() -> None:
    root = MemoryRoot("format-fixture")
    resolver = MemoryIncludeResolver(
        root,
        {
            "app/main.yaml": "payload: !include ../data.json\n",
            "data.json": '{"name":"memory"}',
            "data.toml": "port = 5432\n",
        },
    )
    loader = YamlConfigLoader(include_resolver=resolver, search_paths=[root], allowed_roots=[root])

    result = loader.load_include(
        StringIO("payload: !include ../data.json\ndatabase: !include ../data.toml\n"),
        source=MemorySource(root, PurePosixPath("app/main.yaml")),
    )

    assert result == {"payload": {"name": "memory"}, "database": {"port": 5432}}

    limited_loader = YamlConfigLoader(
        include_resolver=resolver,
        search_paths=[root],
        allowed_roots=[root],
        max_include_files=1,
    )
    with pytest.raises(yaml.YAMLError, match="maximum include count"):
        limited_loader.load_include(
            StringIO("payload: !include ../data.json\ndatabase: !include ../data.toml\n"),
            source=MemorySource(root, PurePosixPath("app/main.yaml")),
        )


def test_memory_resolver_failure_keeps_include_error_boundary() -> None:
    root = MemoryRoot("missing-format-fixture")
    loader = YamlConfigLoader(
        include_resolver=MemoryIncludeResolver(root, {}),
        search_paths=[root],
        allowed_roots=[root],
    )

    with pytest.raises(yaml.YAMLError, match=r"memory include file 'missing\.json' was not found"):
        loader.load_include(
            StringIO("value: !include missing.json\n"),
            source=MemorySource(root, PurePosixPath("main.yaml")),
        )
