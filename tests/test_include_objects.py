"""Explicit include mappings retain scalar behavior and shared resource boundaries."""

from __future__ import annotations

from io import BytesIO, StringIO
from pathlib import Path, PurePosixPath

import pytest
import yaml
from yaml import Node

from yaml_compose import (
    IncludeLimits,
    IncludeRequest,
    JsonPathLimits,
    MemoryIncludeResolver,
    MemoryRoot,
    MemorySource,
    ResolvedInclude,
    YamlConfigLoader,
    get_include_route,
    load_yaml_compose,
    load_yaml_file,
)


def load_object(tag: str, options: dict[str, object], files: dict[str, str | bytes]) -> object:
    root = MemoryRoot("objects")
    body = tag + " " + yaml.safe_dump(options, default_flow_style=True)
    return load_yaml_compose(StringIO(body), include_resolver=MemoryIncludeResolver(root, files), allowed_roots=[root])


@pytest.mark.parametrize(
    ("tag", "path", "content", "expected"),
    [
        ("!include", "data.yaml", "a: 1\n", {"a": 1}),
        ("!include-all", "data.yaml", "---\na: 1\n---\nb: 2\n", [{"a": 1}, {"b": 2}]),
        ("!include-table", "data.csv", "a,b\n1,2\n", [["a", "b"], ["1", "2"]]),
        ("!include-text", "data.txt", "\ufeffhéllo\r\n \t", "\ufeffhéllo\r\n \t"),
        ("!include-raw", "data.bin", b"\x00\xff\r\n", b"\x00\xff\r\n"),
    ],
)
def test_path_only_mapping_preserves_each_tag_result(
    tag: str, path: str, content: str | bytes, expected: object
) -> None:
    assert load_object(tag, {"path": path}, {path: content}) == expected


@pytest.mark.parametrize("tag", ["!include", "!include-all", "!include-table", "!include-text", "!include-raw"])
@pytest.mark.parametrize("shape", ["{}", "[]", "{path: ''}", "{path: null}", "{path: true}", "{path: [x]}"])
def test_invalid_object_shape_is_a_marked_error(tag: str, shape: str) -> None:
    with pytest.raises(yaml.YAMLError) as error:
        load_yaml_compose(StringIO(f"value: {tag} {shape}\n"))
    assert getattr(error.value, "problem_mark", None) is not None
    assert get_include_route(error.value)[-1].tag == tag


@pytest.mark.parametrize(
    "spec",
    [
        "!include {path: x, path: y}",
        "!include {path: x, unknown: true}",
        "!include {path: x, json_pointer: /a, json_path: '$.a'}",
        "!include {path: x, json_pointer: '#/a'}",
        "!include {path: x, json_pointer: /bad~2}",
        "!include {path: x, json_pointer: null}",
        "!include {path: x, json_path: ''}",
        "!include {path: x, json_path: true}",
        "!include {path: x, json_pointer: !include value.yaml}",
        "!include {path: x, json_path: !include query.txt}",
        "!include {<<: {path: x}}",
        "!include {42: x, path: x}",
        "!include {path: !include other.yaml}",
        "!include-text {path: x, json_pointer: ''}",
        "!include-raw {path: x, json_path: '$'}",
        "!include-text {path: x, encoding: missing-encoding-xyz}",
        "!include-text {path: x, encoding: base64_codec}",
        "!include-text {path: x, encoding: null}",
        "!include-text {path: x, lines: [1, 2]}",
        "!include-text {path: x, lines: !custom {start: 1}}",
        "!include-text {path: x, lines: {start: 0}}",
        "!include-text {path: x, lines: {end: -1}}",
        "!include-text {path: x, lines: {start: true}}",
        "!include-text {path: x, lines: {start: !!int true}}",
        "!include-text {path: x, lines: {start: 3, end: 2}}",
        "!include-text {path: x, lines: {start: 1, start: 2}}",
        "!include-text {path: x, lines: {step: 2}}",
        "!include-raw {path: x, offset: -1}",
        "!include-raw {path: x, offset: true}",
        "!include-raw {path: x, length: '2'}",
        "!include-raw {path: x, length: 2.5}",
        "!include-raw {path: x, length: null}",
        "!include-raw {path: x, encoding: utf-8}",
        "!include-raw {path: x, allowed_roots: ['/']}",
    ],
)
def test_invalid_options_fail_before_resolving_or_opening(spec: str) -> None:
    root = MemoryRoot("never-resolve")

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            raise AssertionError(f"invalid options reached resolver: {request.name}")

    with pytest.raises(yaml.YAMLError):
        load_yaml_compose(StringIO(spec), include_resolver=Resolver(), allowed_roots=[root])


def test_option_tags_are_not_evaluated() -> None:
    called = False

    class Loader(yaml.SafeLoader):
        pass

    def construct(loader: Loader, node: Node) -> str:
        nonlocal called
        called = True
        return "secret.txt"

    Loader.add_constructor("!custom", construct)
    with pytest.raises(yaml.YAMLError, match="path must be a string"):
        load_yaml_compose(StringIO("!include-text {path: !custom value}"), yaml_loader=Loader)
    assert not called


@pytest.mark.parametrize(
    ("encoding", "text"),
    [("utf-16", "héllo\r\n"), ("latin-1", "café"), ("utf-8-sig", "no BOM"), ("ascii", "plain")],
)
def test_text_encoding_is_explicit_and_strict(encoding: str, text: str) -> None:
    assert load_object("!include-text", {"path": "data", "encoding": encoding}, {"data": text.encode(encoding)}) == text
    with pytest.raises(yaml.YAMLError, match="not ascii text") as error:
        load_object("!include-text", {"path": "data", "encoding": "ascii"}, {"data": b"\xff"})
    assert isinstance(error.value.__cause__, UnicodeDecodeError)


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        ({}, "one\r\ntwo\rthree\nfour\u2028still-four"),
        ({"start": 2, "end": 3}, "two\rthree\n"),
        ({"end": 1}, "one\r\n"),
        ({"start": 4}, "four\u2028still-four"),
        ({"start": 5}, ""),
        ({"start": 2, "end": 99}, "two\rthree\nfour\u2028still-four"),
    ],
)
def test_text_lines_are_one_based_inclusive_and_preserve_terminators(lines: dict[str, int], expected: str) -> None:
    content = "one\r\ntwo\rthree\nfour\u2028still-four"
    assert load_object("!include-text", {"path": "data", "lines": lines}, {"data": content}) == expected


def test_line_ranges_do_not_hide_invalid_text_outside_range() -> None:
    with pytest.raises(yaml.YAMLError, match="not UTF-8 text"):
        load_object("!include-text", {"path": "data", "lines": {"end": 1}}, {"data": b"valid\ninvalid\xff"})


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, b"\x00\xffabcdef"),
        ({"offset": 2}, b"abcdef"),
        ({"offset": 2, "length": 3}, b"abc"),
        ({"length": 2}, b"\x00\xff"),
        ({"length": 0}, b""),
        ({"offset": 8}, b""),
        ({"offset": 999999}, b""),
        ({"offset": 7, "length": 999999}, b"f"),
    ],
)
def test_raw_byte_ranges_are_zero_based_and_clip_at_eof(options: dict[str, int], expected: bytes) -> None:
    assert load_object("!include-raw", {"path": "data", **options}, {"data": b"\x00\xffabcdef"}) == expected


@pytest.mark.parametrize("tag", ["!include", "!include-all", "!include-table"])
def test_mapping_jsonpath_preserves_nodelist_semantics_and_configured_limits(
    monkeypatch: pytest.MonkeyPatch, tag: str
) -> None:
    limits = JsonPathLimits(max_matches=3)
    expected = [["a", "b"]] if tag == "!include-table" else [{"a": 1}] if tag == "!include-all" else {"a": 1}
    source = "a,b\n" if tag == "!include-table" else "a: 1\n"
    path = "data.csv" if tag == "!include-table" else "data.yaml"
    calls: list[tuple[object, str, JsonPathLimits]] = []

    def select(value: object, query: str, profile: JsonPathLimits) -> list[object]:
        calls.append((value, query, profile))
        return [value]

    monkeypatch.setattr("yaml_compose.loader.select_jsonpath", select)
    root = MemoryRoot("query")
    result = load_yaml_compose(
        StringIO(f"{tag} {{path: {path}, json_path: '$'}}"),
        allowed_roots=[root],
        include_resolver=MemoryIncludeResolver(root, {path: source}),
        jsonpath_limits=limits,
    )
    assert result == [expected]
    assert calls == [(expected, "$", limits)]


def test_jsonpath_input_type_and_query_limits_apply_to_mapping() -> None:
    root = MemoryRoot("query-limits")
    files = {"data.yaml": "when: 2026-11-12\n"}
    with pytest.raises(yaml.YAMLError, match="found date"):
        load_yaml_compose(
            StringIO("!include {path: data.yaml, json_path: '$'}"),
            allowed_roots=[root],
            include_resolver=MemoryIncludeResolver(root, files),
        )
    with pytest.raises(yaml.YAMLError, match="maximum query bytes"):
        load_yaml_compose(
            StringIO("!include {path: data.yaml, json_path: '$.when'}"),
            allowed_roots=[root],
            include_resolver=MemoryIncludeResolver(root, files),
            jsonpath_limits=JsonPathLimits(max_query_bytes=1),
        )


@pytest.mark.parametrize(
    "spec",
    [
        "!include {path: data.yaml, json_pointer: /value}",
        "!include-all {path: data.yaml, json_pointer: /0/value}",
        "!include-text {path: data.yaml, lines: {end: 1}}",
        "!include-raw {path: data.yaml, length: 1}",
    ],
)
@pytest.mark.parametrize(
    "limits",
    [
        IncludeLimits(max_file_bytes=1),
        IncludeLimits(max_total_include_bytes=1),
        IncludeLimits(max_include_files=0),
        IncludeLimits(max_include_depth=0),
    ],
)
def test_selection_never_bypasses_full_source_budgets(spec: str, limits: IncludeLimits) -> None:
    root = MemoryRoot("budgets")
    with pytest.raises(yaml.YAMLError, match=r"limit|maximum"):
        load_yaml_compose(
            StringIO(spec),
            allowed_roots=[root],
            include_resolver=MemoryIncludeResolver(root, {"data.yaml": "value: 1\n"}),
            include_limits=limits,
        )


@pytest.mark.parametrize("tag", ["!include", "!include-all", "!include-table", "!include-text", "!include-raw"])
def test_object_sources_remain_confined_before_opening(tag: str) -> None:
    allowed, outside = MemoryRoot("allowed"), MemoryRoot("outside")
    opened = False

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            def open_binary() -> BytesIO:
                nonlocal opened
                opened = True
                return BytesIO(b"unsafe")

            return ResolvedInclude(MemorySource(outside, PurePosixPath("data")), open_binary)

    with pytest.raises(yaml.YAMLError, match="outside the allowed roots"):
        load_yaml_compose(StringIO(f"{tag} {{path: data}}"), allowed_roots=[allowed], include_resolver=Resolver())
    assert not opened


def test_custom_resolver_stream_closes_before_text_decode_failure() -> None:
    root = MemoryRoot("close")
    stream = BytesIO(b"\xff")

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            return ResolvedInclude(MemorySource(root, PurePosixPath("data")), lambda: stream)

    with pytest.raises(yaml.YAMLError, match="not ascii text"):
        load_yaml_compose(
            StringIO("!include-text {path: data, encoding: ascii}"),
            allowed_roots=[root],
            include_resolver=Resolver(),
        )
    assert stream.closed


def test_mapping_paths_work_with_custom_loaders_aliases_and_nested_routes(test_path: Path) -> None:
    (test_path / "data#;%.txt").write_bytes(b"one\r\ntwo\r\n")
    (test_path / "main.yaml").write_text(
        "%LOADER YamlCompose\n---\nname: &name 'data#;%.txt'\ntext: !include-text {path: *name, lines: {start: 2}}\n",
        encoding="utf-8",
    )

    class Loader(yaml.SafeLoader):
        pass

    assert load_yaml_file(test_path / "main.yaml", yaml_loader=Loader) == {"name": "data#;%.txt", "text": "two\r\n"}
    root = MemoryRoot("routes")
    loader = YamlConfigLoader(
        allowed_roots=[root],
        include_resolver=MemoryIncludeResolver(root, {"outer.yaml": "!include-raw {path: missing, length: 1}"}),
    )
    with pytest.raises(yaml.YAMLError) as error:
        loader.load_include(StringIO("!include {path: outer.yaml}"))
    assert [frame.requested_path for frame in get_include_route(error.value)] == ["outer.yaml", "missing"]


def test_generic_codec_unicode_error_retains_include_context() -> None:
    with pytest.raises(yaml.YAMLError, match="not idna text") as error:
        load_object("!include-text", {"path": "data", "encoding": "idna"}, {"data": b"xn--"})
    assert isinstance(error.value.__cause__, UnicodeError)
    assert get_include_route(error.value)[-1].requested_path == "data"


def test_legacy_scalar_constructor_hook_is_preserved_but_mapping_options_are_literal() -> None:
    class Loader(yaml.SafeLoader):
        def construct_scalar(self, node: yaml.ScalarNode | yaml.MappingNode) -> str:
            value = super().construct_scalar(node)
            return "actual.yaml" if node.tag == "!include" and value == "alias" else value

    root = MemoryRoot("custom-scalar")
    resolver = MemoryIncludeResolver(root, {"actual.yaml": "value: 1", "alias": "value: 2"})
    assert load_yaml_compose(
        StringIO("!include alias"), yaml_loader=Loader, allowed_roots=[root], include_resolver=resolver
    ) == {"value": 1}
    assert load_yaml_compose(
        StringIO("!include {path: alias}"), yaml_loader=Loader, allowed_roots=[root], include_resolver=resolver
    ) == {"value": 2}


def test_range_options_follow_yaml_integer_notation() -> None:
    root = MemoryRoot("yaml-integers")
    assert (
        load_yaml_compose(
            StringIO("!include-raw {path: data, offset: 0x2, length: 01}"),
            allowed_roots=[root],
            include_resolver=MemoryIncludeResolver(root, {"data": b"abcdef"}),
        )
        == b"c"
    )
