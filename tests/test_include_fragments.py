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
    result = loader.load_include(StringIO('<<<: !include "hardware.yaml#/arrays/kbr"\nnested: {b: 3}\n'))
    assert result == {"gain": 4, "nested": {"a": 1, "b": 3}}
    assert loader.load_include(StringIO('array: !include "hardware.yaml#/arrays/kbr"\n')) == {
        "array": {"gain": 4, "nested": {"a": 1, "b": 2}}
    }


def test_sequence_selection_and_spread() -> None:
    loader = memory_loader({"base.yaml": "steps: [one, {name: two}]\n"})
    assert loader.load_include(StringIO('items: [before, !spread [!include "base.yaml#/steps"], after]\n')) == {
        "items": ["before", "one", {"name": "two"}, "after"]
    }
    assert loader.load_include(StringIO('!include "base.yaml#/steps/1/name"\n')) == "two"


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
def test_fragment_selection_across_structured_formats(name: str, content: str, pointer: str, expected: object) -> None:
    loader = memory_loader({name: content})
    assert loader.load_include(StringIO(f'!include "{name}#{pointer}"\n')) == expected


def test_csv_row_and_cell_selection() -> None:
    loader = memory_loader({"data.csv": "name,age\nAda,37\n"})
    assert loader.load_include(StringIO('!include-table "data.csv#/1"\n')) == ["Ada", "37"]
    assert loader.load_include(StringIO('!include-table "data.csv#/1/0"\n')) == "Ada"
    with pytest.raises(yaml.YAMLError, match="table sources require"):
        loader.load_include(StringIO('!include "data.csv#/1"\n'))


@pytest.mark.parametrize("tag", ["!include", "!include-text", "!include-raw"])
def test_root_fragment_preserves_whole_value(tag: str) -> None:
    loader = memory_loader({"data.yaml": "hello\n"})
    expected = {"!include": "hello", "!include-text": "hello\n", "!include-raw": b"hello\n"}[tag]
    assert loader.load_include(StringIO(f'{tag} "data.yaml#"\n')) == expected


@pytest.mark.parametrize("tag", ["!include-text", "!include-raw"])
def test_scalar_tags_do_not_implicitly_index_characters_or_bytes(tag: str) -> None:
    loader = memory_loader({"data.txt": "hello\n"})
    with pytest.raises(yaml.YAMLError, match="cannot select") as caught:
        loader.load_include(StringIO(f'{tag} "data.txt#/0"\n'))
    assert "data.txt#/0" in str(caught.value)


@pytest.mark.parametrize("fragment", ["#/missing", "#/values/01", "#/values/-", "#/value/more", "#bad", "#/%XX"])
def test_errors_identify_include_selector_and_source_mark(fragment: str) -> None:
    loader = memory_loader({"base.yaml": "values: [a]\nvalue: 1\n"})
    with pytest.raises(yaml.YAMLError) as caught:
        loader.load_include(StringIO(f'value: !include "base.yaml{fragment}"\n'))
    assert f"base.yaml{fragment}" in str(caught.value)
    assert fragment in str(caught.value)
    route = get_include_route(caught.value)
    assert route[-1].requested_path == f"base.yaml{fragment}"
    assert route[-1].line == 1
    assert route[-1].column == 8


def test_nested_yaml_is_completed_before_selection_and_preserves_relative_paths() -> None:
    loader = memory_loader(
        {
            "sub/base.yaml": (
                "defaults: !include values.yaml#/cfg\nselected:\n  <<<: !include values.yaml#/cfg\n  other: 3\n"
            ),
            "sub/values.yaml": "cfg: {value: 2}\n",
        }
    )
    assert loader.load_include(StringIO('!include "sub/base.yaml#/selected"\n')) == {"value": 2, "other": 3}


def test_fragment_does_not_hide_invalid_unselected_records() -> None:
    loader = memory_loader({"data.jsonl": '{"ok":true}\nnot-json\n'})
    with pytest.raises(yaml.YAMLError, match="invalid JSONL"):
        loader.load_include(StringIO('!include "data.jsonl#/0"\n'))


def test_fragment_does_not_hide_nested_cycles() -> None:
    loader = memory_loader({"base.yaml": 'selected: 1\nother: !include "base.yaml#/selected"\n'})
    with pytest.raises(yaml.YAMLError, match="include cycle detected"):
        loader.load_include(StringIO('!include "base.yaml#/selected"\n'))


@pytest.mark.parametrize("recursive", [True, False])
def test_yaml_multidocument_stream_remains_distinct_from_sequence(recursive: bool) -> None:
    root = MemoryRoot("multi")
    resolver = MemoryIncludeResolver(root, {"multi.yaml": "---\na: 1\n---\na: 2\n"})
    with pytest.raises(yaml.YAMLError, match="expected a single document"):
        load_yaml_compose(
            StringIO('!include "multi.yaml#/0"\n'),
            include_resolver=resolver,
            allowed_roots=[root],
            recursive_dispatch=recursive,
        )


def test_full_input_budgets_apply_before_fragment_selection() -> None:
    root = MemoryRoot("budget")
    resolver = MemoryIncludeResolver(root, {"data.yaml": "value: 1\nother: a-long-value\n"})
    for limits in (
        IncludeLimits(max_file_bytes=10),
        IncludeLimits(max_total_include_bytes=10),
        IncludeLimits(max_include_files=0),
    ):
        with pytest.raises(yaml.YAMLError, match=r"limit|maximum"):
            load_yaml_compose(
                StringIO('!include "data.yaml#/value"\n'),
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
        load_yaml_compose(StringIO('!include "alias#/value"\n'), include_resolver=Resolver(), allowed_roots=[root])
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
            StringIO('!include "base.yaml#/missing"\n'), include_resolver=Resolver(), allowed_roots=[root]
        )
    assert stream.closed


def test_filesystem_fragment_confines_path_and_preserves_percent_names(test_path: Path) -> None:
    (test_path / "name%20with spaces.yaml").write_text("value: 2\n", encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text('%LOADER YamlCompose\n---\n!include "name%20with spaces.yaml#/value"\n', encoding="utf-8")
    assert load_yaml_file(config) == 2
    outside = test_path / "outside"
    allowed = test_path / "allowed"
    outside.mkdir()
    allowed.mkdir()
    (outside / "secret.yaml").write_text("value: 3\n", encoding="utf-8")
    (allowed / "link.yaml").symlink_to(outside / "secret.yaml")
    (allowed / "main.yaml").write_text('%LOADER YamlCompose\n---\n!include "link.yaml#/value"\n', encoding="utf-8")
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
            StringIO('!include "base.yaml#/value/items/1"\n'),
            include_resolver=resolver,
            allowed_roots=[root],
            yaml_loader=CustomLoader,
            recursive_dispatch=recursive,
        )
        == "b"
    )


def test_shallow_merge_and_replace_consume_selected_mappings() -> None:
    loader = memory_loader({"base.yaml": "cfg: {nested: {one: 1, two: 2}, other: 3}\n"})
    assert loader.load_include(StringIO('<<: !include "base.yaml#/cfg"\nnested: {one: 4}\n')) == {
        "nested": {"one": 4},
        "other": 3,
    }
    assert loader.load_include(StringIO('<<<: {cfg: {stale: true}}\ncfg: !replace [!include "base.yaml#/cfg"]\n')) == {
        "cfg": {"nested": {"one": 1, "two": 2}, "other": 3}
    }


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ('<<<: !include "base.yaml#/number"\n', "must be a mapping"),
        ('values: [!spread [!include "base.yaml#/number"]]\n', "must be a sequence"),
    ],
)
def test_selected_wrong_type_errors_retain_include_route(document: str, message: str) -> None:
    loader = memory_loader({"base.yaml": "number: 3\n"})
    with pytest.raises(yaml.YAMLError, match=message) as caught:
        loader.load_include(StringIO(document))
    assert get_include_route(caught.value)[-1].requested_path == "base.yaml#/number"


def test_json5_fragment_when_optional_parser_is_available() -> None:
    pytest.importorskip("json5")
    loader = memory_loader({"base.json5": "{cfg: {value: 3,},}"})
    assert loader.load_include(StringIO('!include "base.json5#/cfg/value"\n')) == 3


@pytest.mark.parametrize("recursive", [True, False])
def test_cyclic_alias_traversal_is_bounded_by_pointer_length(recursive: bool) -> None:
    root = MemoryRoot("alias-cycle")
    resolver = MemoryIncludeResolver(root, {"base.yaml": "&self {self: *self, value: 2}\n"})
    assert (
        load_yaml_compose(
            StringIO('!include "base.yaml#/self/self/value"\n'),
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
            StringIO('!include "base.custom#/items/1/value"\n'),
            include_resolver=resolver,
            allowed_roots=[root],
            format_registry=formats,
        )
        == "second"
    )
