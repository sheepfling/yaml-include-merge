"""JSONC and optional JSON5 include behavior through YAML composition."""

from __future__ import annotations

import math
from io import BytesIO, StringIO
from pathlib import Path, PurePosixPath
from typing import cast

import pytest
import yaml
from yaml.constructor import ConstructorError

from yaml_compose import (
    FileSource,
    FormatRegistry,
    IncludeRequest,
    MemoryRoot,
    MemorySource,
    ResolvedInclude,
    YamlConfigLoader,
    load_yaml_file,
)
from yaml_compose.formats import FormatDependencyError


def test_registry_detects_jsonc_and_json5_suffixes(test_path: Path) -> None:
    registry = FormatRegistry()
    assert registry.detect_format(FileSource(test_path / "value.jsonc")).format_id == "jsonc"
    assert registry.detect_format(FileSource(test_path / "value.json5")).format_id == "json5"


def test_jsonc_comments_preserve_strings_and_yaml_overlay_semantics(test_path: Path) -> None:
    (test_path / "base.jsonc").write_text(
        "{\n"
        "  // service defaults\n"
        '  "service": { /* inline */ "host": "shared", "path": "https://example.test/a//b",\n'
        '    "note": "escaped quote: \\" // still string" },\n'
        '  "labels": ["shared"]\n'
        "} // trailing comment\n",
        encoding="utf-8",
    )
    config = test_path / "main.yaml"
    config.write_text(
        "%LOADER YamlCompose\n---\n<<<: !include base.jsonc\nservice:\n  host: production\nlabels: []\n",
        encoding="utf-8",
    )

    assert load_yaml_file(config) == {
        "service": {
            "host": "production",
            "path": "https://example.test/a//b",
            "note": 'escaped quote: " // still string',
        },
        "labels": [],
    }


def test_jsonc_masking_preserves_token_separation_and_source_coordinates(test_path: Path) -> None:
    source = test_path / "joined.jsonc"
    source.write_text("1/* comment */2", encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include joined.jsonc\n", encoding="utf-8")

    with pytest.raises(ConstructorError, match=r"invalid JSONC include source.*Extra data.*line 1 column") as caught:
        load_yaml_file(config)
    assert str(source) in str(caught.value)
    assert caught.value.problem_mark is not None
    assert caught.value.__cause__ is not None


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ('{"key": 1, "key": 2}', "duplicate JSON object key 'key'"),
        ('{"value": NaN}', "non-finite JSON constant 'NaN'"),
        ('{"value": 1,}', None),
        ("# not a JSONC comment", "Expecting value"),
    ],
)
def test_jsonc_keeps_strict_json_rules(test_path: Path, contents: str, message: str | None) -> None:
    (test_path / "invalid.jsonc").write_text(contents, encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include invalid.jsonc\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError, match="invalid JSONC include source") as caught:
        load_yaml_file(config)

    if message is not None:
        assert message in str(caught.value)
    assert "invalid.jsonc" in str(caught.value)
    assert caught.value.__cause__ is not None


def test_jsonc_rejects_unterminated_block_comment_after_complete_value(test_path: Path) -> None:
    source = test_path / "unterminated.jsonc"
    source.write_text('{"value": 1}\n/* never closes', encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include unterminated.jsonc\n", encoding="utf-8")

    with pytest.raises(ConstructorError, match=r"unterminated block comment at line 2, column 1") as caught:
        load_yaml_file(config)
    assert str(source) in str(caught.value)
    assert caught.value.problem_mark is not None


def test_jsonc_keeps_crlf_error_positions_and_rejects_utf8_bom(test_path: Path) -> None:
    source = test_path / "broken.jsonc"
    source.write_bytes(b'{\r\n  /* comment */\r\n  "value":,\r\n}\r\n')
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include broken.jsonc\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError, match=r"invalid JSONC include source.*line 3 column 11"):
        load_yaml_file(config)

    source.write_bytes(b'\xef\xbb\xbf{"value":1}')
    with pytest.raises(ConstructorError, match=r"invalid JSONC include source.*UTF-8 BOM"):
        load_yaml_file(config)


def test_json5_parses_extended_syntax_and_yaml_composes_mapping(test_path: Path) -> None:
    (test_path / "base.json5").write_text(
        "// JSON5 defaults\n{service: {host: 'shared', region: 'west'}, labels: ['base',], hex: +0xff,}\n",
        encoding="utf-8",
    )
    (test_path / "steps.json5").write_text("['lint', 'test',]", encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text(
        "%LOADER YamlCompose\n---\n<<<: !include base.json5\nservice:\n  host: production\n"
        "labels: [release]\nsteps: [prepare, !spread [!include steps.json5], deploy]\n",
        encoding="utf-8",
    )

    assert load_yaml_file(config) == {
        "service": {"host": "production", "region": "west"},
        "labels": ["release"],
        "hex": 255,
        "steps": ["prepare", "lint", "test", "deploy"],
    }


def test_json5_preserves_scalar_values_special_floats_and_overflow(test_path: Path) -> None:
    (test_path / "values.json5").write_text(
        "{nan: NaN, positive: Infinity, negative: -Infinity, overflow: 1e400, negative_overflow: -1e400}\n",
        encoding="utf-8",
    )
    (test_path / "scalar.json5").write_text("'root value'", encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text(
        "%LOADER YamlCompose\n---\nvalues: !include values.json5\nscalar: !include scalar.json5\n",
        encoding="utf-8",
    )

    result = load_yaml_file(config)
    assert isinstance(result, dict)
    values = result["values"]
    assert isinstance(values, dict)
    raw_values = cast(dict[object, object], values)
    assert all(isinstance(key, str) for key in raw_values)
    typed_values = {key: value for key, value in raw_values.items() if isinstance(key, str)}
    assert len(typed_values) == len(raw_values)
    nan_value = typed_values["nan"]
    positive = typed_values["positive"]
    negative = typed_values["negative"]
    overflow = typed_values["overflow"]
    negative_overflow = typed_values["negative_overflow"]
    assert isinstance(nan_value, float) and math.isnan(nan_value)
    assert isinstance(positive, float) and positive == math.inf
    assert isinstance(negative, float) and negative == -math.inf
    assert isinstance(overflow, float) and overflow == math.inf
    assert isinstance(negative_overflow, float) and negative_overflow == -math.inf
    assert result["scalar"] == "root value"


@pytest.mark.parametrize("contents", ["{a: 1, a: 2}", "{'a': 1, '\\u0061': 2}"])
def test_json5_rejects_duplicate_keys_including_escaped_equivalence(test_path: Path, contents: str) -> None:
    source = test_path / "duplicate.json5"
    source.write_text(contents, encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include duplicate.json5\n", encoding="utf-8")

    with pytest.raises(ConstructorError, match=r"invalid JSON5 include source.*Duplicate key") as caught:
        load_yaml_file(config)
    assert str(source) in str(caught.value)
    assert caught.value.problem_mark is not None
    assert isinstance(caught.value.__cause__, ValueError)


def test_json5_rejects_multiple_roots_and_preserves_parser_error_context(test_path: Path) -> None:
    source = test_path / "extra.json5"
    source.write_text("{} true", encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include extra.json5\n", encoding="utf-8")

    with pytest.raises(ConstructorError, match=r"invalid JSON5 include source.*Unexpected.*column") as caught:
        load_yaml_file(config)
    assert str(source) in str(caught.value)
    assert caught.value.problem_mark is not None
    assert isinstance(caught.value.__cause__, ValueError)

    source.write_text("{\n value: [1,\n}", encoding="utf-8")
    with pytest.raises(ConstructorError, match=r"invalid JSON5 include source.*<string>:3.*column") as caught:
        load_yaml_file(config)
    assert str(source) in str(caught.value)


def test_json5_rejects_bom_and_invalid_utf8(test_path: Path) -> None:
    source = test_path / "bad.json5"
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include bad.json5\n", encoding="utf-8")
    source.write_bytes(b"\xef\xbb\xbf{}")

    with pytest.raises(ConstructorError, match=r"invalid JSON5 include source.*BOM"):
        load_yaml_file(config)

    source.write_bytes(b"\xff")
    with pytest.raises(ConstructorError, match=r"invalid JSON5 include source.*not UTF-8 text"):
        load_yaml_file(config)


def test_json5_parser_import_is_lazy_for_yaml_json_jsonc_and_toml(
    test_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yaml_compose.formats as formats

    config = test_path / "main.yaml"
    (test_path / "base.json").write_text('{"value": 1}', encoding="utf-8")
    (test_path / "base.jsonc").write_text('{/* comment */"value": 2}', encoding="utf-8")
    (test_path / "base.toml").write_text("value = 3\n", encoding="utf-8")
    (test_path / "base.json5").write_text("{value: 4}", encoding="utf-8")
    config.write_text("value: plain_yaml\n", encoding="utf-8")
    original = formats.importlib.import_module
    calls: list[str] = []

    def track(name: str) -> object:
        if name == "json5":
            calls.append(name)
        return original(name)

    monkeypatch.setattr(formats.importlib, "import_module", track)
    assert load_yaml_file(config) == {"value": "plain_yaml"}
    config.write_text(
        "%LOADER YamlCompose\n---\njson: !include base.json\njsonc: !include base.jsonc\ntoml: !include base.toml\n",
        encoding="utf-8",
    )
    assert load_yaml_file(config) == {"json": {"value": 1}, "jsonc": {"value": 2}, "toml": {"value": 3}}
    assert calls == []

    config.write_text("%LOADER YamlCompose\n---\nvalue: !include base.json5\n", encoding="utf-8")
    assert load_yaml_file(config) == {"value": {"value": 4}}
    assert calls == ["json5"]


def test_missing_json5_dependency_is_marked_and_does_not_break_other_formats(
    test_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yaml_compose.formats as formats

    config = test_path / "main.yaml"
    (test_path / "base.json").write_text('{"value": 1}', encoding="utf-8")
    (test_path / "base.json5").write_text("{value: 5}", encoding="utf-8")
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include base.json\n", encoding="utf-8")
    original = formats.importlib.import_module

    def unavailable(name: str) -> object:
        if name == "json5":
            raise ModuleNotFoundError("No module named 'json5'", name="json5")
        return original(name)

    monkeypatch.setattr(formats.importlib, "import_module", unavailable)
    assert load_yaml_file(config) == {"value": {"value": 1}}
    assert YamlConfigLoader().load_file(config) == {"value": {"value": 1}}

    config.write_text("%LOADER YamlCompose\n---\nvalue: !include base.json5\n", encoding="utf-8")
    with pytest.raises(
        ConstructorError, match=r"cannot parse JSON5 include source.*optional 'json5' dependency"
    ) as caught:
        load_yaml_file(config)
    assert isinstance(caught.value.__cause__, FormatDependencyError)
    assert isinstance(caught.value.__cause__.__cause__, ModuleNotFoundError)


def test_custom_resolver_dispatches_using_resolved_jsonc_and_json5_suffixes(test_path: Path) -> None:
    for suffix, content, expected in (
        (".jsonc", b'{/* layer */"value":"jsonc"}', {"value": "jsonc"}),
        (".json5", b"{value: 'json5'}", {"value": "json5"}),
    ):
        actual = test_path / f"actual{suffix}"
        actual.write_bytes(content)
        config = test_path / "main.yaml"
        config.write_text("%LOADER YamlCompose\n---\npayload: !include alias.yaml\n", encoding="utf-8")

        class RedirectResolver:
            def __init__(self, path: Path) -> None:
                self.path = path

            def resolve(self, request: IncludeRequest) -> ResolvedInclude:
                return ResolvedInclude(FileSource(self.path), lambda: self.path.open("rb"))

        assert load_yaml_file(config, include_resolver=RedirectResolver(actual)) == {"payload": expected}


def test_nested_yaml_includes_resolve_jsonc_and_json5_relative_to_parent(test_path: Path) -> None:
    layers = test_path / "layers"
    layers.mkdir()
    (layers / "defaults.jsonc").write_text('{"service":{"host":"base"}}', encoding="utf-8")
    (layers / "region.json5").write_text("{service: {region: 'west'}}", encoding="utf-8")
    (layers / "layer.yaml").write_text(
        "<<<: [!include defaults.jsonc, !include region.json5]\nservice:\n  host: production\n",
        encoding="utf-8",
    )
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\n<<<: !include layers/layer.yaml\n", encoding="utf-8")

    assert load_yaml_file(config) == {"service": {"host": "production", "region": "west"}}


@pytest.mark.parametrize(
    ("suffix", "content"),
    [(".jsonc", b'{"value":"large"}'), (".json5", b"{value: 'large'}")],
)
def test_jsonc_and_json5_share_allowed_roots_and_per_file_budgets(test_path: Path, suffix: str, content: bytes) -> None:
    allowed = test_path / "allowed"
    allowed.mkdir()
    inside = allowed / f"inside{suffix}"
    inside.write_bytes(content)
    outside = test_path / f"outside{suffix}"
    outside.write_bytes(content)
    config = allowed / "main.yaml"
    config.write_text(f"%LOADER YamlCompose\n---\nvalue: !include inside{suffix}\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError, match="per-file size limit"):
        load_yaml_file(config, max_file_bytes=4)

    class OutsideResolver:
        opened = False

        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            return ResolvedInclude(FileSource(outside), self.open)

        def open(self):
            self.opened = True
            return outside.open("rb")

    resolver = OutsideResolver()
    with pytest.raises(yaml.YAMLError, match="outside the allowed roots"):
        load_yaml_file(config, allowed_roots=[allowed], include_resolver=resolver)
    assert not resolver.opened


def test_cross_format_includes_obey_total_byte_and_file_count_budgets(test_path: Path) -> None:
    (test_path / "first.jsonc").write_text('{"first":1}', encoding="utf-8")
    (test_path / "second.json5").write_text("{second: 2}", encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text(
        "%LOADER YamlCompose\n---\nfirst: !include first.jsonc\nsecond: !include second.json5\n",
        encoding="utf-8",
    )
    total = len((test_path / "first.jsonc").read_bytes()) + len((test_path / "second.json5").read_bytes()) - 1

    with pytest.raises(yaml.YAMLError, match="maximum total included bytes"):
        load_yaml_file(config, max_total_include_bytes=total)
    with pytest.raises(yaml.YAMLError, match="maximum include count"):
        load_yaml_file(config, max_include_files=1)


@pytest.mark.parametrize(
    ("suffix", "content"),
    [(".jsonc", b'{"value":1}'), (".json5", b"{value: 1}")],
)
def test_jsonc_and_json5_share_nested_yaml_depth_budget(test_path: Path, suffix: str, content: bytes) -> None:
    layers = test_path / "layers"
    layers.mkdir()
    (layers / f"base{suffix}").write_bytes(content)
    (layers / "layer.yaml").write_text(f"value: !include base{suffix}\n", encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nlayer: !include layers/layer.yaml\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError, match="maximum include depth"):
        load_yaml_file(config, max_include_depth=1)


def test_jsonc_and_json5_streams_close_when_parsing_fails() -> None:
    root = MemoryRoot("jsonc-json5-stream-cleanup")
    for suffix, content in (("bad.jsonc", b'{"value":1,}'), ("bad.json5", b"{value:")):
        source = MemorySource(root, PurePosixPath(suffix))

        class TrackingResolver:
            def __init__(self, source: MemorySource, content: bytes) -> None:
                self.source = source
                self.stream = BytesIO(content)

            def resolve(self, request: IncludeRequest) -> ResolvedInclude:
                return ResolvedInclude(self.source, lambda: self.stream)

        resolver = TrackingResolver(source, content)
        loader = YamlConfigLoader(include_resolver=resolver, search_paths=[root], allowed_roots=[root])
        with pytest.raises(yaml.YAMLError):
            loader.load_include(
                StringIO(f"value: !include {suffix}\n"),
                source=MemorySource(root, PurePosixPath("main.yaml")),
            )
        assert resolver.stream.closed
