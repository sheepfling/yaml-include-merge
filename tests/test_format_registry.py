"""Per-loader parser registry contracts and the shared include boundary."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO, StringIO
from pathlib import Path
from typing import ClassVar

import pytest
import yaml
from yaml.constructor import ConstructorError

from yaml_compose import (
    FileSource,
    FormatAdapter,
    FormatDescriptor,
    FormatInput,
    FormatParseError,
    FormatRegistry,
    IncludeRequest,
    ResolvedInclude,
    UnknownFormatError,
    YamlConfigLoader,
    load_yaml_compose,
    load_yaml_dispatch,
    load_yaml_file,
)
from yaml_compose.formats import FormatDependencyError


@dataclass(frozen=True, slots=True)
class ExampleFormat:
    """Small independent adapter used to exercise the public registration API."""

    prefix: str = "plugin"
    descriptor: ClassVar[FormatDescriptor] = FormatDescriptor("example", (".example", ".rows"))

    def parse(self, source: FormatInput, /) -> object:
        text = source.data.decode("utf-8")
        if source.source.path.suffix.lower() == ".rows":
            return [f"{self.prefix}:{item}" for item in text.splitlines()]
        return {"service": {"name": f"{self.prefix}:{text.strip()}"}}


def _registry(adapter: FormatAdapter | None = None) -> FormatRegistry:
    registry = FormatRegistry()
    if adapter is not None:
        registry.register(adapter)
    return registry


def test_independent_adapter_uses_yaml_merge_and_spread_without_core_edits(test_path: Path) -> None:
    (test_path / "defaults.example").write_text("shared", encoding="utf-8")
    (test_path / "tasks.rows").write_text("lint\ntest\n", encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text(
        "%LOADER YamlCompose\n---\n<<<: !include defaults.example\n"
        "service:\n  environment: production\n"
        "steps: [prepare, !spread [!include tasks.rows], deploy]\n",
        encoding="utf-8",
    )
    registry = _registry(ExampleFormat())

    assert load_yaml_file(config, format_registry=registry) == {
        "service": {"name": "plugin:shared", "environment": "production"},
        "steps": ["prepare", "plugin:lint", "plugin:test", "deploy"],
    }


def test_custom_adapter_tuple_value_is_preserved_for_object_typed_consumers(test_path: Path) -> None:
    class TupleAdapter:
        descriptor: ClassVar[FormatDescriptor] = FormatDescriptor("tuple-plugin", (".tuple-plugin",))

        def parse(self, source: FormatInput, /) -> tuple[str, ...]:
            return tuple(source.data.decode("utf-8").split())

    (test_path / "value.tuple-plugin").write_text("one two", encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include value.tuple-plugin\n", encoding="utf-8")
    registry = _registry(TupleAdapter())

    assert load_yaml_file(config, format_registry=registry) == {"value": ("one", "two")}


def test_all_public_load_entrypoints_accept_a_registry(test_path: Path) -> None:
    (test_path / "value.example").write_text("shared", encoding="utf-8")
    path = test_path / "main.yaml"
    path.write_text("%LOADER YamlCompose\n---\nvalue: !include value.example\n", encoding="utf-8")
    registry = _registry(ExampleFormat())
    loader = YamlConfigLoader(format_registry=registry)

    expected = {"value": {"service": {"name": "plugin:shared"}}}
    assert load_yaml_file(path, format_registry=registry) == expected
    assert loader.load_file(path) == expected
    assert (
        load_yaml_dispatch(
            StringIO(path.read_text(encoding="utf-8")),
            loader_tag="YamlCompose",
            search_paths=[test_path],
            format_registry=registry,
        )
        == expected
    )
    assert (
        load_yaml_compose(
            StringIO(path.read_text(encoding="utf-8")), search_paths=[test_path], format_registry=registry
        )
        == expected
    )

    configured_loader = YamlConfigLoader(format_registry=registry, search_paths=[test_path])
    assert (
        configured_loader.load_stream(StringIO("%LOADER YamlCompose\n---\nvalue: !include value.example\n")) == expected
    )
    assert configured_loader.load_include(StringIO("value: !include value.example\n")) == expected


def test_loader_captures_registry_snapshot_and_loaders_can_be_configured_independently(test_path: Path) -> None:
    (test_path / "value.example").write_text("shared", encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include value.example\n", encoding="utf-8")
    registry = _registry(ExampleFormat("old"))
    old_loader = YamlConfigLoader(format_registry=registry)

    registry.register(ExampleFormat("new"), replace=True)
    new_loader = YamlConfigLoader(format_registry=registry)

    assert old_loader.load_file(config) == {"value": {"service": {"name": "old:shared"}}}
    assert new_loader.load_file(config) == {"value": {"service": {"name": "new:shared"}}}
    with pytest.raises(TypeError, match="read-only"):
        old_loader.format_registry.register(ExampleFormat("mutate"), replace=True)


def test_registration_conflicts_and_replacement_are_atomic() -> None:
    registry = _registry(ExampleFormat())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(ExampleFormat("duplicate"))

    @dataclass(frozen=True, slots=True)
    class Collision:
        descriptor: ClassVar[FormatDescriptor] = FormatDescriptor("collision", (".collision", ".example"))

        def parse(self, source: FormatInput, /) -> object:
            return source.data

    with pytest.raises(ValueError, match="already owned by format 'example'"):
        registry.register(Collision())

    with pytest.raises(UnknownFormatError, match="'collision'"):
        registry.get_format("collision")
    assert registry.detect_format(FileSource(Path("value.collision"))).format_id == "yaml"
    assert registry.get_format("example").suffixes == (".example", ".rows")
    selection = registry.select_format(FileSource(Path("value.example")))
    assert selection.descriptor.format_id == "example"
    assert selection.adapter is not None

    registry.register(ExampleFormat("replacement"), replace=True)
    assert registry.get_format("example").format_id == "example"
    assert registry.detect_format(FileSource(Path("value.rows"))).format_id == "example"


def test_same_id_replacement_cannot_steal_suffix_or_partially_mutate_registry() -> None:
    registry = _registry(ExampleFormat())

    @dataclass(frozen=True, slots=True)
    class Owner:
        descriptor: ClassVar[FormatDescriptor] = FormatDescriptor("owner", (".claimed",))

        def parse(self, source: FormatInput, /) -> object:
            return source.data

    @dataclass(frozen=True, slots=True)
    class Replacement:
        descriptor: ClassVar[FormatDescriptor] = FormatDescriptor("example", (".replacement", ".claimed"))

        def parse(self, source: FormatInput, /) -> object:
            return source.data

    registry.register(Owner())
    with pytest.raises(ValueError, match="already owned by format 'owner'"):
        registry.register(Replacement(), replace=True)

    assert registry.get_format("example").suffixes == (".example", ".rows")
    assert registry.detect_format(FileSource(Path("value.example"))).format_id == "example"
    assert registry.detect_format(FileSource(Path("value.replacement"))).format_id == "yaml"
    assert registry.detect_format(FileSource(Path("value.claimed"))).format_id == "owner"


def test_unregister_is_deterministic_and_keeps_yaml_reserved() -> None:
    registry = _registry(ExampleFormat())
    registry.unregister("example")

    with pytest.raises(UnknownFormatError, match="format ID 'example' is not registered"):
        registry.get_format("example")
    assert registry.detect_format(FileSource(Path("value.example"))).format_id == "yaml"
    with pytest.raises(ValueError, match="YAML composition entry cannot be removed"):
        registry.unregister("yaml")


def test_explicit_format_selection_overrides_suffix_and_unknown_id_is_marked(test_path: Path) -> None:
    actual = test_path / "actual.yaml"
    actual.write_text("shared", encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include alias.yaml\n", encoding="utf-8")

    class Resolver:
        def __init__(self, format_id: str | None) -> None:
            self.format_id = format_id
            self.opened = False

        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            def open_binary():
                self.opened = True
                return actual.open("rb")

            return ResolvedInclude(FileSource(actual), open_binary, self.format_id)

    registry = _registry(ExampleFormat())
    selected = Resolver("example")
    assert load_yaml_file(config, include_resolver=selected, format_registry=registry) == {
        "value": {"service": {"name": "plugin:shared"}}
    }

    missing = Resolver("not-registered")
    with pytest.raises(ConstructorError, match="format ID 'not-registered' is not registered") as caught:
        load_yaml_file(config, include_resolver=missing, format_registry=registry)
    assert not missing.opened
    assert isinstance(caught.value.__cause__, UnknownFormatError)
    assert caught.value.problem_mark is not None


def test_plugin_parse_errors_keep_source_mark_and_exception_chain(test_path: Path) -> None:
    @dataclass(frozen=True, slots=True)
    class BrokenAdapter:
        descriptor: ClassVar[FormatDescriptor] = FormatDescriptor("broken", (".broken",))

        def parse(self, source: FormatInput, /) -> object:
            try:
                raise ValueError("bad row 4")
            except ValueError as exc:
                raise FormatParseError(str(exc)) from exc

    (test_path / "bad.broken").write_bytes(b"broken")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include bad.broken\n", encoding="utf-8")
    stream = BytesIO(b"broken")

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            return ResolvedInclude(FileSource(test_path / "bad.broken"), lambda: stream)

    with pytest.raises(ConstructorError, match=r"invalid BROKEN include source.*bad row 4") as caught:
        load_yaml_file(config, include_resolver=Resolver(), format_registry=_registry(BrokenAdapter()))

    assert stream.closed
    assert caught.value.problem_mark is not None
    assert caught.value.problem_mark.name == str(config)
    assert isinstance(caught.value.__cause__, FormatParseError)
    assert isinstance(caught.value.__cause__.__cause__, ValueError)


def test_adapters_receive_closed_stream_bytes_and_canonical_source(test_path: Path) -> None:
    actual = test_path / "actual.example"
    actual.write_bytes(b"shared")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include alias.example\n", encoding="utf-8")
    opened: list[BytesIO] = []

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            stream = BytesIO(b"shared")
            opened.append(stream)
            return ResolvedInclude(FileSource(actual), lambda: stream)

    class BoundaryAdapter:
        descriptor: ClassVar[FormatDescriptor] = FormatDescriptor("boundary", (".example",))

        def parse(self, source: FormatInput, /) -> object:
            assert isinstance(source.data, bytes)
            assert source.source == FileSource(actual)
            assert source.source_name == str(actual)
            assert opened[0].closed
            return source.data.decode("utf-8")

    registry = FormatRegistry()
    registry.register(BoundaryAdapter())
    assert load_yaml_file(config, include_resolver=Resolver(), format_registry=registry) == {"value": "shared"}
    assert len(opened) == 1


def test_custom_yaml_constructor_result_is_preserved_through_nested_include(test_path: Path) -> None:
    from yaml import SafeLoader

    @dataclass(frozen=True)
    class DomainValue:
        value: str

    class AppLoader(SafeLoader):
        pass

    def construct_domain(loader: SafeLoader, node: yaml.ScalarNode) -> DomainValue:
        return DomainValue(loader.construct_scalar(node))

    AppLoader.add_constructor("!domain", construct_domain)
    (test_path / "nested.yaml").write_text("value: !domain native\n", encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nnested: !include nested.yaml\n", encoding="utf-8")

    result = load_yaml_file(config, yaml_loader=AppLoader)
    assert result == {"nested": {"value": DomainValue("native")}}
    direct = load_yaml_compose(StringIO("!domain native\n"), yaml_loader=AppLoader)
    assert direct == DomainValue("native")


def test_plugin_uses_shared_root_and_byte_limits_before_parsing(test_path: Path) -> None:
    allowed = test_path / "allowed"
    allowed.mkdir()
    outside = test_path / "outside.example"
    outside.write_bytes(b"large plugin content")
    inside = allowed / "inside.example"
    inside.write_bytes(b"large plugin content")
    config = allowed / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\nvalue: !include alias.example\n", encoding="utf-8")

    class Resolver:
        def __init__(self, source: Path) -> None:
            self.source = source
            self.opened = False

        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            return ResolvedInclude(FileSource(self.source), self.open)

        def open(self):
            self.opened = True
            return self.source.open("rb")

    outside_resolver = Resolver(outside)
    with pytest.raises(yaml.YAMLError, match="outside the allowed roots"):
        load_yaml_file(
            config,
            allowed_roots=[allowed],
            include_resolver=outside_resolver,
            format_registry=_registry(ExampleFormat()),
        )
    assert not outside_resolver.opened

    inside_resolver = Resolver(inside)
    with pytest.raises(yaml.YAMLError, match="per-file size limit"):
        load_yaml_file(
            config,
            include_resolver=inside_resolver,
            format_registry=_registry(ExampleFormat()),
            max_file_bytes=4,
        )
    assert inside_resolver.opened


def test_toml_parser_dependency_is_lazy_and_missing_dependency_is_marked(
    test_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yaml_compose.formats as formats

    (test_path / "base.json").write_text('{"value":1}', encoding="utf-8")
    (test_path / "base.toml").write_text("value = 1\n", encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\njson: !include base.json\n", encoding="utf-8")
    original_import_module = formats.importlib.import_module
    calls: list[str] = []

    def unavailable(name: str) -> object:
        if name in {"tomllib", "tomli"}:
            calls.append(name)
            raise ModuleNotFoundError(name=name)
        return original_import_module(name)

    monkeypatch.setattr(formats.importlib, "import_module", unavailable)
    assert load_yaml_file(config) == {"json": {"value": 1}}
    assert calls == []

    config.write_text("%LOADER YamlCompose\n---\ntoml: !include base.toml\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError, match="cannot parse TOML include source") as caught:
        load_yaml_file(config)
    assert isinstance(caught.value.__cause__, FormatDependencyError)
    assert calls == ["tomllib", "tomli"]
