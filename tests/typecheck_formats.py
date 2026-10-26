"""Static checks for default and injected parser result types."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar, TextIO

from typing_extensions import assert_type
from yaml import SafeLoader

from yaml_compose import (
    FormatCapability,
    FormatDescriptor,
    FormatInput,
    FormatRegistry,
    RecordFormatAdapter,
    YamlConfigLoader,
    YamlType,
    load_yaml_compose,
    load_yaml_dispatch,
    load_yaml_file,
)


class DomainLoader(SafeLoader):
    """Example application loader whose constructors may return domain objects."""


class PluginAdapter:
    descriptor: ClassVar[FormatDescriptor] = FormatDescriptor("plugin", (".plugin",))

    def parse(self, source: FormatInput, /) -> object:
        return source.data.decode("utf-8")


class PluginRecordAdapter:
    descriptor: ClassVar[FormatDescriptor] = FormatDescriptor(
        "record-plugin", (".record-plugin",), capability="records"
    )

    def iter_records(self, source: FormatInput, /) -> Iterator[object]:
        yield source.data.decode("utf-8")


def check_default_result_types(stream: TextIO, path: Path) -> None:
    """Default built-in parsers retain the bounded YAML result type."""
    assert_type(load_yaml_dispatch(stream), YamlType)
    assert_type(load_yaml_dispatch(stream, format_registry=None), YamlType)
    assert_type(load_yaml_compose(stream), YamlType)
    assert_type(load_yaml_compose(stream, format_registry=None), YamlType)
    assert_type(load_yaml_file(path), YamlType)
    assert_type(load_yaml_file(path, format_registry=None), YamlType)

    loader = YamlConfigLoader()
    none_registry_loader = YamlConfigLoader(format_registry=None)
    assert_type(loader.load_stream(stream), YamlType)
    assert_type(none_registry_loader.load_stream(stream), YamlType)
    assert_type(loader.load_include(stream), YamlType)
    assert_type(loader.load_file(path), YamlType)


def check_existing_consumer_annotations(stream: TextIO, path: Path) -> None:
    """Keep the prior YamlType return annotation valid for ordinary callers."""

    def load_default() -> YamlType:
        return load_yaml_compose(stream)

    def load_with_configured_default() -> YamlType:
        return YamlConfigLoader().load_file(path)

    assert_type(load_default(), YamlType)
    assert_type(load_with_configured_default(), YamlType)


def check_bare_loader_annotations(loader: YamlConfigLoader, path: Path) -> YamlType:
    """Existing bare loader annotations default to the built-in result type."""
    return loader.load_file(path)


def check_injected_result_types(stream: TextIO, path: Path) -> None:
    """Custom parsers and YAML loader constructors preserve the honest object type."""
    formats = FormatRegistry()
    formats.register(PluginAdapter())
    assert_type(load_yaml_dispatch(stream, format_registry=formats), object)
    assert_type(load_yaml_compose(stream, format_registry=formats), object)
    assert_type(load_yaml_file(path, format_registry=formats), object)

    format_loader = YamlConfigLoader(format_registry=formats)
    assert_type(format_loader.load_stream(stream), object)
    assert_type(format_loader.load_include(stream), object)
    assert_type(format_loader.load_file(path), object)

    assert_type(load_yaml_compose(stream, yaml_loader=DomainLoader), object)
    assert_type(load_yaml_compose(stream, yaml_loader=SafeLoader), object)
    domain_loader = YamlConfigLoader(yaml_loader=DomainLoader)
    assert_type(domain_loader.load_stream(stream), object)
    assert_type(domain_loader.load_file(path), object)
    explicit_safe_loader = YamlConfigLoader(yaml_loader=SafeLoader)
    assert_type(explicit_safe_loader.load_file(path), object)


def check_record_adapter_contract() -> None:
    formats = FormatRegistry()
    adapter: RecordFormatAdapter = PluginRecordAdapter()
    formats.register(adapter)
    assert_type(formats.get_format("record-plugin").capability, FormatCapability)
