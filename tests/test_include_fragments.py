from __future__ import annotations

from io import BytesIO, StringIO
from pathlib import Path, PurePosixPath
from typing import ClassVar

import pytest
import yaml

from yaml_compose import (
    FormatDescriptor,
    FormatInput,
    FormatRegistry,
    IncludeLimits,
    IncludeRequest,
    MemoryIncludeResolver,
    MemoryRoot,
    MemorySource,
    ResolvedInclude,
    YamlConfigLoader,
    get_include_route,
    load_yaml_compose,
    load_yaml_file,
)


def memory_loader(files: dict[str, str | bytes]) -> YamlConfigLoader:
    root = MemoryRoot("fragments")
    return YamlConfigLoader(include_resolver=MemoryIncludeResolver(root, files), allowed_roots=[root])


def test_mapping_selection_precedes_composition() -> None:
    loader = memory_loader({"hardware.yaml": "arrays:\n  kbr:\n    gain: 4\n    nested: {a: 1, b: 2}\nother: 7\n"})
    result = loader.load_include(
        StringIO('<<<: !include {path: "hardware.yaml", json_pointer: "/arrays/kbr"}\nnested: {b: 3}\n')
    )
    assert result == {"gain": 4, "nested": {"a": 1, "b": 3}}
    assert loader.load_include(StringIO('array: !include {path: "hardware.yaml", json_pointer: "/arrays/kbr"}\n')) == {
        "array": {"gain": 4, "nested": {"a": 1, "b": 2}}
    }


def test_sequence_selection_and_spread() -> None:
    loader = memory_loader({"base.yaml": "steps: [one, {name: two}]\n"})
    assert loader.load_include(
        StringIO('items: [before, !spread [!include {path: "base.yaml", json_pointer: "/steps"}], after]\n')
    ) == {"items": ["before", "one", {"name": "two"}, "after"]}
    assert loader.load_include(StringIO('!include {path: "base.yaml", json_pointer: "/steps/1/name"}\n')) == "two"


@pytest.mark.parametrize(
    ("name", "content", "pointer", "expected"),
    [
        ("data.json", '{"cfg":{"value":2}}', "/cfg/value", 2),
        ("data.jsonc", '{/* note */ "cfg":{"value":2}}', "/cfg/value", 2),
        ("data.toml", "[cfg]\nvalue = 2\n", "/cfg/value", 2),
        ("data.ini", "[cfg]\nvalue = 2\n", "/cfg/value", "2"),
        (".env", "VALUE=2\n", "/VALUE", "2"),
        ("data.jsonl", '{"value":1}\n{"value":2}\n', "/1/value", 2),
        ("data.ndjson", '{"value":1}\n{"value":2}\n', "/1/value", 2),
    ],
)
def test_pointer_selection_across_structured_formats(name: str, content: str, pointer: str, expected: object) -> None:
    loader = memory_loader({name: content})
    assert loader.load_include(StringIO(f'!include {{path: "{name}", json_pointer: "{pointer}"}}\n')) == expected


def test_csv_row_and_cell_selection() -> None:
    loader = memory_loader({"data.csv": "name,age\nAda,37\n"})
    with pytest.raises(yaml.YAMLError, match="unknown"):
        loader.load_include(StringIO('!include-table {path: "data.csv", json_pointer: "/1"}\n'))
    with pytest.raises(yaml.YAMLError, match="unknown"):
        loader.load_include(StringIO('!include-table {path: "data.csv", json_pointer: "/1/0"}\n'))
    with pytest.raises(yaml.YAMLError, match="table sources require"):
        loader.load_include(StringIO('!include {path: "data.csv", json_pointer: "/1"}\n'))


def test_empty_pointer_preserves_whole_structured_value() -> None:
    loader = memory_loader({"data.yaml": "hello\n"})
    assert loader.load_include(StringIO('!include {path: "data.yaml", json_pointer: ""}\n')) == "hello"


@pytest.mark.parametrize("tag", ["!include-text", "!include-raw"])
@pytest.mark.parametrize("pointer", ["", "/0"])
def test_scalar_tags_reject_structured_selectors(tag: str, pointer: str) -> None:
    loader = memory_loader({"data.txt": "hello\n"})
    with pytest.raises(yaml.YAMLError) as caught:
        loader.load_include(StringIO(f'{tag} {{path: "data.txt", json_pointer: "{pointer}"}}\n'))
    assert "json_pointer" in str(caught.value)


@pytest.mark.parametrize("pointer", ["/missing", "/values/01", "/values/-", "/value/more", "bad", "/~2"])
def test_errors_identify_include_selector_and_source_mark(pointer: str) -> None:
    loader = memory_loader({"base.yaml": "values: [a]\nvalue: 1\n"})
    with pytest.raises(yaml.YAMLError) as caught:
        loader.load_include(StringIO(f'value: !include {{path: "base.yaml", json_pointer: "{pointer}"}}\n'))
    assert "base.yaml" in str(caught.value)
    assert pointer in str(caught.value)
    route = get_include_route(caught.value)
    assert route[-1].requested_path == "base.yaml"
    assert route[-1].line == 1
    assert route[-1].column == 8


def test_nested_yaml_is_completed_before_selection_and_preserves_relative_paths() -> None:
    loader = memory_loader(
        {
            "sub/base.yaml": (
                "defaults: !include {path: values.yaml, json_pointer: /cfg}\nselected:\n"
                "  <<<: !include {path: values.yaml, json_pointer: /cfg}\n  other: 3\n"
            ),
            "sub/values.yaml": "cfg: {value: 2}\n",
        }
    )
    assert loader.load_include(StringIO('!include {path: "sub/base.yaml", json_pointer: "/selected"}\n')) == {
        "value": 2,
        "other": 3,
    }


def test_pointer_does_not_hide_invalid_unselected_records() -> None:
    loader = memory_loader({"data.jsonl": '{"ok":true}\nnot-json\n'})
    with pytest.raises(yaml.YAMLError, match="invalid JSONL"):
        loader.load_include(StringIO('!include {path: "data.jsonl", json_pointer: "/0"}\n'))


def test_pointer_does_not_hide_nested_cycles() -> None:
    loader = memory_loader(
        {"base.yaml": 'selected: 1\nother: !include {path: "base.yaml", json_pointer: "/selected"}\n'}
    )
    with pytest.raises(yaml.YAMLError, match="include cycle detected"):
        loader.load_include(StringIO('!include {path: "base.yaml", json_pointer: "/selected"}\n'))


@pytest.mark.parametrize("recursive", [True, False])
def test_yaml_multidocument_stream_remains_distinct_from_sequence(recursive: bool) -> None:
    root = MemoryRoot("multi")
    resolver = MemoryIncludeResolver(root, {"multi.yaml": "---\na: 1\n---\na: 2\n"})
    with pytest.raises(yaml.YAMLError, match="expected a single document"):
        load_yaml_compose(
            StringIO('!include {path: "multi.yaml", json_pointer: "/0"}\n'),
            include_resolver=resolver,
            allowed_roots=[root],
            recursive_dispatch=recursive,
        )


def test_full_input_budgets_apply_before_pointer_selection() -> None:
    root = MemoryRoot("budget")
    resolver = MemoryIncludeResolver(root, {"data.yaml": "value: 1\nother: a-long-value\n"})
    for limits in (
        IncludeLimits(max_file_bytes=10),
        IncludeLimits(max_total_include_bytes=10),
        IncludeLimits(max_include_files=0),
    ):
        with pytest.raises(yaml.YAMLError, match=r"limit|maximum"):
            load_yaml_compose(
                StringIO('!include {path: "data.yaml", json_pointer: "/value"}\n'),
                include_resolver=resolver,
                allowed_roots=[root],
                include_limits=limits,
            )


def test_resolver_receives_only_local_name_and_controls_format() -> None:
    root = MemoryRoot("custom")
    requests: list[IncludeRequest] = []
    streams: list[BytesIO] = []

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            requests.append(request)

            def open_binary() -> BytesIO:
                stream = BytesIO(b'{"value":42}')
                streams.append(stream)
                return stream

            return ResolvedInclude(MemorySource(root, PurePosixPath("opaque")), open_binary, format_id="json")

    assert (
        load_yaml_compose(
            StringIO('!include {path: "alias", json_pointer: "/value"}\n'),
            include_resolver=Resolver(),
            allowed_roots=[root],
        )
        == 42
    )
    assert requests[0].name == "alias"
    assert streams[0].closed


def test_selector_failure_closes_stream() -> None:
    root = MemoryRoot("closed")
    stream = BytesIO(b"value: 2\n")

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            return ResolvedInclude(MemorySource(root, PurePosixPath(request.name)), lambda: stream)

    with pytest.raises(yaml.YAMLError, match="cannot select"):
        load_yaml_compose(
            StringIO('!include {path: "base.yaml", json_pointer: "/missing"}\n'),
            include_resolver=Resolver(),
            allowed_roots=[root],
        )
    assert stream.closed


def test_filesystem_pointer_confines_path_and_preserves_percent_names(test_path: Path) -> None:
    (test_path / "name%20with spaces.yaml").write_text("value: 2\n", encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text(
        '%LOADER YamlCompose\n---\n!include {path: "name%20with spaces.yaml", json_pointer: "/value"}\n',
        encoding="utf-8",
    )
    assert load_yaml_file(config) == 2
    outside = test_path / "outside"
    allowed = test_path / "allowed"
    outside.mkdir()
    allowed.mkdir()
    (outside / "secret.yaml").write_text("value: 3\n", encoding="utf-8")
    (allowed / "link.yaml").symlink_to(outside / "secret.yaml")
    (allowed / "main.yaml").write_text(
        '%LOADER YamlCompose\n---\n!include {path: "link.yaml", json_pointer: "/value"}\n', encoding="utf-8"
    )
    with pytest.raises(yaml.YAMLError, match="outside the allowed roots"):
        load_yaml_file(allowed / "main.yaml")


@pytest.mark.parametrize("recursive", [True, False])
def test_selection_with_custom_yaml_constructor(recursive: bool) -> None:
    root = MemoryRoot("custom-yaml")
    resolver = MemoryIncludeResolver(root, {"base.yaml": "value: !custom ignored\n"})

    class CustomLoader(yaml.SafeLoader):
        pass

    def construct_value(loader: yaml.SafeLoader, node: yaml.Node) -> object:
        return {"items": ("a", "b")}

    CustomLoader.add_constructor("!custom", construct_value)
    assert (
        load_yaml_compose(
            StringIO('!include {path: "base.yaml", json_pointer: "/value/items/1"}\n'),
            include_resolver=resolver,
            allowed_roots=[root],
            yaml_loader=CustomLoader,
            recursive_dispatch=recursive,
        )
        == "b"
    )


def test_shallow_merge_and_replace_consume_selected_mappings() -> None:
    loader = memory_loader({"base.yaml": "cfg: {nested: {one: 1, two: 2}, other: 3}\n"})
    assert loader.load_include(
        StringIO('<<: !include {path: "base.yaml", json_pointer: "/cfg"}\nnested: {one: 4}\n')
    ) == {
        "nested": {"one": 4},
        "other": 3,
    }
    assert loader.load_include(
        StringIO('<<<: {cfg: {stale: true}}\ncfg: !replace [!include {path: "base.yaml", json_pointer: "/cfg"}]\n')
    ) == {"cfg": {"nested": {"one": 1, "two": 2}, "other": 3}}


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ('<<<: !include {path: "base.yaml", json_pointer: "/number"}\n', "must be a mapping"),
        ('values: [!spread [!include {path: "base.yaml", json_pointer: "/number"}]]\n', "must be a sequence"),
    ],
)
def test_selected_wrong_type_errors_retain_include_route(document: str, message: str) -> None:
    loader = memory_loader({"base.yaml": "number: 3\n"})
    with pytest.raises(yaml.YAMLError, match=message) as caught:
        loader.load_include(StringIO(document))
    assert get_include_route(caught.value)[-1].requested_path == "base.yaml"


def test_json5_pointer_when_optional_parser_is_available() -> None:
    pytest.importorskip("json5")
    loader = memory_loader({"base.json5": "{cfg: {value: 3,},}"})
    assert loader.load_include(StringIO('!include {path: "base.json5", json_pointer: "/cfg/value"}\n')) == 3


@pytest.mark.parametrize("recursive", [True, False])
def test_cyclic_alias_traversal_is_bounded_by_pointer_length(recursive: bool) -> None:
    root = MemoryRoot("alias-cycle")
    resolver = MemoryIncludeResolver(root, {"base.yaml": "&self {self: *self, value: 2}\n"})
    assert (
        load_yaml_compose(
            StringIO('!include {path: "base.yaml", json_pointer: "/self/self/value"}\n'),
            include_resolver=resolver,
            allowed_roots=[root],
            recursive_dispatch=recursive,
        )
        == 2
    )


def test_custom_adapter_values_use_the_same_pointer_semantics() -> None:
    class Adapter:
        descriptor: ClassVar[FormatDescriptor] = FormatDescriptor("custom", (".custom",))

        def parse(self, source: FormatInput, /) -> object:
            assert source.data == b"opaque"
            return {"items": ("first", {"value": "second"})}

    formats = FormatRegistry()
    formats.register(Adapter())
    root = MemoryRoot("adapter")
    resolver = MemoryIncludeResolver(root, {"base.custom": b"opaque"})
    assert (
        load_yaml_compose(
            StringIO('!include {path: "base.custom", json_pointer: "/items/1/value"}\n'),
            include_resolver=resolver,
            allowed_roots=[root],
            format_registry=formats,
        )
        == "second"
    )


@pytest.mark.parametrize(
    ("tag", "name", "content", "expected"),
    [
        ("!include", "config#blue%20.yaml", "value: 2\n", {"value": 2}),
        ("!include-table", "table#blue%20.csv", "a,b\n", [["a", "b"]]),
        ("!include-text", "notes#blue%20.txt", "hello\n", "hello\n"),
        ("!include-raw", "data#blue%20.bin", "hello\n", b"hello\n"),
    ],
)
def test_scalar_paths_keep_literal_hash_and_percent(tag: str, name: str, content: str, expected: object) -> None:
    loader = memory_loader({name: content})
    assert loader.load_include(StringIO(f'{tag} "{name}"\n')) == expected


def test_path_and_pointer_use_separate_literal_names() -> None:
    loader = memory_loader({"data#v1%20.json": '{"a%20b":1,"a b":2,"#":3}'})
    assert loader.load_include(StringIO('!include {path: "data#v1%20.json", json_pointer: "/a%20b"}')) == 1
    assert loader.load_include(StringIO('!include {path: "data#v1%20.json", json_pointer: "/a b"}')) == 2
    assert loader.load_include(StringIO('!include {path: "data#v1%20.json", json_pointer: "/#"}')) == 3


def test_filesystem_scalar_path_keeps_literal_hash(test_path: Path) -> None:
    source = test_path / "data#v1%20.yaml"
    source.write_text("value: 2\n", encoding="utf-8")
    main = test_path / "main.yaml"
    main.write_text('%LOADER YamlCompose\n---\n!include "data#v1%20.yaml"\n', encoding="utf-8")
    assert load_yaml_file(main) == {"value": 2}
