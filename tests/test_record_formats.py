"""Record-format adapters consumed through the shared YAML include boundary."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import ClassVar

import pytest
import yaml
from yaml.constructor import ConstructorError

from yaml_compose import (
    FileSource,
    FormatDescriptor,
    FormatInput,
    FormatParseError,
    FormatRegistry,
    IncludeRequest,
    NdjsonAdapter,
    RecordFormatAdapter,
    ResolvedInclude,
    YamlConfigLoader,
    load_yaml_file,
)


def _config(path: Path, body: str) -> Path:
    config = path / "main.yaml"
    config.write_text(f"%LOADER YamlCompose\n---\n{body}", encoding="utf-8")
    return config


def test_registry_marks_jsonl_and_ndjson_as_record_capabilities(test_path: Path) -> None:
    registry = FormatRegistry()

    assert registry.detect_format(FileSource(test_path / "events.jsonl")).capability == "records"
    assert registry.detect_format(FileSource(test_path / "events.ndjson")).capability == "records"
    assert registry.get_format("json").capability == "document"


def test_jsonl_preserves_record_order_and_all_json_root_shapes(test_path: Path) -> None:
    (test_path / "records.jsonl").write_bytes(
        '{"name":"café"}\r\n[1,2]\n"line\u2028break\u2029"\nnull\ntrue\n-3.5'.encode("utf-8")
    )
    config = _config(
        test_path,
        "records: !include records.jsonl\nspread: [before, !spread [!include records.jsonl], after]\n",
    )

    records = [{"name": "café"}, [1, 2], "line\u2028break\u2029", None, True, -3.5]
    assert load_yaml_file(config) == {
        "records": records,
        "spread": ["before", *records, "after"],
    }


@pytest.mark.parametrize("suffix", [".jsonl", ".ndjson"])
def test_record_files_accept_empty_input_and_optional_final_newline(test_path: Path, suffix: str) -> None:
    (test_path / f"empty{suffix}").write_bytes(b"")
    (test_path / f"events{suffix}").write_bytes(b"1\r\nnull")
    config = _config(
        test_path,
        f"empty: !include empty{suffix}\nevents: !include events{suffix}\n",
    )

    assert load_yaml_file(config) == {"empty": [], "events": [1, None]}


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        (b"\n", "JSONL record line 1: blank records are not allowed"),
        (b"1\n\n2\n", "JSONL record line 2: blank records are not allowed"),
        (b"1\n  \n2", "JSONL record line 2: blank records are not allowed"),
        (b'{"ok":true}\nnot-json\n', "JSONL record line 2, column 1"),
        (b'{"id":1,"id":2}\n', "JSONL record line 1: duplicate JSON object key"),
        (b"1\nInfinity", "JSONL record line 2: non-finite JSON constant"),
        (b"1\r2", "JSONL record line 1, column 3: Extra data"),
        (b"\xef\xbb\xbf{}\n", "byte-order mark"),
        (b"\xff\n", "is not UTF-8 text"),
    ],
)
def test_jsonl_rejects_invalid_records_with_source_and_line_context(
    test_path: Path, contents: bytes, message: str
) -> None:
    source = test_path / "bad.jsonl"
    source.write_bytes(contents)
    config = _config(test_path, "value: !include bad.jsonl\n")

    with pytest.raises(ConstructorError, match=message) as caught:
        load_yaml_file(config)

    assert str(source) in str(caught.value)
    assert caught.value.problem_mark is not None
    assert caught.value.__cause__ is not None


def test_jsonl_splits_only_lf_not_unicode_separators_or_bare_cr(test_path: Path) -> None:
    (test_path / "unicode.jsonl").write_text('"a\u2028b\u2029c"\n2', encoding="utf-8")
    (test_path / "bare-cr.jsonl").write_bytes(b"1\r2")
    config = _config(test_path, 'value: !include "unicode.jsonl"\n')

    assert load_yaml_file(config) == {"value": ["a\u2028b\u2029c", 2]}

    config.write_text("%LOADER YamlCompose\n---\nvalue: !include bare-cr.jsonl\n", encoding="utf-8")
    with pytest.raises(ConstructorError, match=r"JSONL record line 1, column 3: Extra data"):
        load_yaml_file(config)


def test_ndjson_rejects_blank_records_by_default(test_path: Path) -> None:
    (test_path / "events.ndjson").write_bytes(b"1\n\n2\n")
    config = _config(test_path, "events: !include events.ndjson\n")

    with pytest.raises(ConstructorError, match="NDJSON record line 2: blank records are not allowed"):
        load_yaml_file(config)


def test_ndjson_blank_line_ignoring_is_an_explicit_per_loader_adapter_option(test_path: Path) -> None:
    (test_path / "events.ndjson").write_bytes(b"\n1\n \t\nnull\r\n2")
    config = _config(test_path, "events: !include events.ndjson\n")
    registry = FormatRegistry()
    registry.register(NdjsonAdapter(ignore_blank_lines=True), replace=True)
    loader = YamlConfigLoader(format_registry=registry)

    assert loader.load_file(config) == {"events": [1, None, 2]}


def test_ndjson_blank_line_policy_is_captured_per_loader(test_path: Path) -> None:
    (test_path / "events.ndjson").write_bytes(b"1\n\n2\n")
    config = _config(test_path, "events: !include events.ndjson\n")
    registry = FormatRegistry()
    strict_loader = YamlConfigLoader(format_registry=registry)

    registry.register(NdjsonAdapter(ignore_blank_lines=True), replace=True)
    ignoring_loader = YamlConfigLoader(format_registry=registry)

    assert ignoring_loader.load_file(config) == {"events": [1, 2]}
    with pytest.raises(ConstructorError, match="NDJSON record line 2: blank records are not allowed"):
        strict_loader.load_file(config)


def test_record_result_is_not_implicitly_a_mapping_merge_source(test_path: Path) -> None:
    (test_path / "records.jsonl").write_text('{"value":1}\n', encoding="utf-8")
    config = _config(test_path, "<<<: !include records.jsonl\n")

    with pytest.raises(ConstructorError, match=r"each source for <<< must be a mapping") as caught:
        load_yaml_file(config)
    assert caught.value.problem_mark is not None


def test_nested_yaml_can_include_records_relative_to_its_source(test_path: Path) -> None:
    layers = test_path / "layers"
    layers.mkdir()
    (test_path / "tasks.jsonl").write_text('"lint"\n"test"\n', encoding="utf-8")
    (layers / "config.yaml").write_text("tasks: [prepare, !spread [!include ../tasks.jsonl]]\n", encoding="utf-8")
    config = _config(test_path, "<<<: !include layers/config.yaml\n")

    assert load_yaml_file(config) == {"tasks": ["prepare", "lint", "test"]}


def test_record_include_obeys_shared_byte_and_file_budgets_once_per_source(test_path: Path) -> None:
    source = test_path / "records.jsonl"
    source.write_bytes(b"1\n2\n3\n")
    config = _config(test_path, "records: !include records.jsonl\n")

    assert load_yaml_file(config, max_file_bytes=len(source.read_bytes())) == {"records": [1, 2, 3]}
    assert load_yaml_file(
        config,
        max_file_bytes=len(source.read_bytes()),
        max_total_include_bytes=len(source.read_bytes()),
        max_include_files=1,
    ) == {"records": [1, 2, 3]}

    with pytest.raises(yaml.YAMLError, match="per-file size limit"):
        load_yaml_file(config, max_file_bytes=len(source.read_bytes()) - 1)
    with pytest.raises(yaml.YAMLError, match="maximum total included bytes"):
        load_yaml_file(config, max_total_include_bytes=len(source.read_bytes()) - 1)


def test_record_include_obeys_nested_yaml_depth_and_include_count_limits(test_path: Path) -> None:
    (test_path / "records.jsonl").write_text("1\n", encoding="utf-8")
    (test_path / "nested.yaml").write_text("records: !include records.jsonl\n", encoding="utf-8")
    nested = _config(test_path, "<<<: !include nested.yaml\n")

    with pytest.raises(yaml.YAMLError, match="maximum include depth"):
        load_yaml_file(nested, max_include_depth=1)

    repeated = _config(test_path, "first: !include records.jsonl\nsecond: !include records.jsonl\n")
    with pytest.raises(yaml.YAMLError, match="maximum include count"):
        load_yaml_file(repeated, max_include_files=1)


def test_custom_resolved_suffix_selects_record_adapter_and_closes_source_stream(test_path: Path) -> None:
    target = test_path / "actual.ndjson"
    target.write_bytes(b'"one"\n"two"')
    stream = BytesIO(target.read_bytes())
    config = _config(test_path, "records: !include alias.yaml\n")

    class RedirectResolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            return ResolvedInclude(FileSource(target), lambda: stream)

    assert load_yaml_file(config, include_resolver=RedirectResolver()) == {"records": ["one", "two"]}
    assert stream.closed


@dataclass(frozen=True, slots=True)
class SemicolonRecordAdapter:
    """Independent public record adapter used by the registry integration test."""

    descriptor: ClassVar[FormatDescriptor] = FormatDescriptor("semicolon-records", (".records",), "records")

    def iter_records(self, source: FormatInput, /) -> Iterator[object]:
        text = source.data.decode("utf-8")
        for item in text.split(";"):
            if item:
                yield item


def test_independent_record_adapter_registers_and_uses_yaml_spread(test_path: Path) -> None:
    (test_path / "steps.records").write_text("lint;test", encoding="utf-8")
    config = _config(test_path, "items: [prepare, !spread [!include steps.records], deploy]\n")
    registry = FormatRegistry()
    adapter: RecordFormatAdapter = SemicolonRecordAdapter()
    registry.register(adapter)

    assert load_yaml_file(config, format_registry=registry) == {"items": ["prepare", "lint", "test", "deploy"]}


def test_late_record_generator_failure_is_marked_and_iterator_is_closed(test_path: Path) -> None:
    (test_path / "source.failure").write_text("ignored", encoding="utf-8")
    config = _config(test_path, "partial: !include source.failure\n")

    @dataclass
    class FailingAdapter:
        descriptor: ClassVar[FormatDescriptor] = FormatDescriptor("failing-records", (".failure",), "records")
        closed: bool = False

        def iter_records(self, source: FormatInput, /) -> Iterator[object]:
            try:
                yield "partial"
                raise FormatParseError("record line 2: late invalid value")
            finally:
                self.closed = True

    adapter = FailingAdapter()
    registry = FormatRegistry()
    registry.register(adapter)
    loader = YamlConfigLoader(format_registry=registry)

    with pytest.raises(ConstructorError, match=r"invalid FAILING-RECORDS include source.*late invalid value") as caught:
        loader.load_file(config)

    assert adapter.closed
    assert str(test_path / "source.failure") in str(caught.value)
    assert caught.value.problem_mark is not None
    assert isinstance(caught.value.__cause__, FormatParseError)


def test_successful_record_generator_is_closed_after_full_materialization(test_path: Path) -> None:
    (test_path / "source.complete").write_text("ignored", encoding="utf-8")
    config = _config(test_path, "items: !include source.complete\n")

    @dataclass
    class ClosingAdapter:
        descriptor: ClassVar[FormatDescriptor] = FormatDescriptor("closing-records", (".complete",), "records")
        closed: bool = False

        def iter_records(self, source: FormatInput, /) -> Iterator[object]:
            try:
                yield "complete"
            finally:
                self.closed = True

    adapter = ClosingAdapter()
    registry = FormatRegistry()
    registry.register(adapter)

    assert load_yaml_file(config, format_registry=registry) == {"items": ["complete"]}
    assert adapter.closed


def test_record_capability_requires_an_iterator_method() -> None:
    class InvalidRecordAdapter:
        descriptor: ClassVar[FormatDescriptor] = FormatDescriptor("invalid-records", (".invalid",), "records")

        def parse(self, source: FormatInput, /) -> object:
            return source.data

    with pytest.raises(TypeError, match="record adapters must provide an iter_records"):
        FormatRegistry().register(InvalidRecordAdapter())
