"""CSV table includes use an explicit row shape through the shared adapter registry."""

from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import pytest
import yaml
from yaml.constructor import ConstructorError

from yaml_compose import (
    CsvAdapter,
    FileSource,
    FormatParseError,
    FormatRegistry,
    IncludeRequest,
    ResolvedInclude,
    YamlConfigLoader,
    load_yaml_file,
)


def _config(path: Path, body: str) -> Path:
    config = path / "main.yaml"
    config.write_text(f"%LOADER YamlCompose\n---\n{body}", encoding="utf-8")
    return config


def test_csv_table_uses_row_lists_by_default(test_path: Path) -> None:
    source = test_path / "rows.csv"
    source.write_text("id,enabled\n001,true\n", encoding="utf-8")
    config = _config(test_path, "rows: !include-table rows.csv\n")
    descriptor = FormatRegistry().detect_format(FileSource(source))

    assert descriptor.format_id == "csv"
    assert descriptor.capability == "records"
    assert load_yaml_file(config) == {"rows": [["id", "enabled"], ["001", "true"]]}
    registry = FormatRegistry()
    registry.register(CsvAdapter(), replace=True)
    assert load_yaml_file(config, format_registry=registry) == {"rows": [["id", "enabled"], ["001", "true"]]}


def test_include_table_can_read_its_own_source_without_recursing(test_path: Path) -> None:
    source = test_path / "main.csv"
    source.write_bytes(b"%LOADER YamlCompose\n---\nrows: !include-table main.csv\n")

    assert load_yaml_file(source) == {"rows": [["%LOADER YamlCompose"], ["---"], ["rows: !include-table main.csv"]]}


def test_ordinary_include_rejects_csv_with_a_table_tag_hint_and_does_not_open_it(test_path: Path) -> None:
    source = test_path / "rows.csv"
    source.write_text("one,two\n", encoding="utf-8")
    config = _config(test_path, "rows: !include rows.csv\n")
    opened = False

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            nonlocal opened

            def open_binary() -> BytesIO:
                nonlocal opened
                opened = True
                return BytesIO(source.read_bytes())

            return ResolvedInclude(FileSource(source), open_binary)

    with pytest.raises(ConstructorError, match=r"CSV sources require !include-table") as caught:
        load_yaml_file(config, include_resolver=Resolver())

    assert "while resolving !include" in str(caught.value)
    assert caught.value.problem_mark is not None
    assert not opened


def test_include_table_rejects_a_non_csv_source(test_path: Path) -> None:
    (test_path / "settings.yaml").write_text("value: 1\n", encoding="utf-8")
    config = _config(test_path, "table: !include-table settings.yaml\n")

    with pytest.raises(ConstructorError, match=r"!include-table requires a CSV table source") as caught:
        load_yaml_file(config)

    assert "while resolving !include-table" in str(caught.value)
    assert caught.value.problem_mark is not None


def test_csv_row_lists_preserve_quoting_newlines_unicode_and_strings(test_path: Path) -> None:
    source = test_path / "rows.csv"
    source.write_bytes('name,note,value\n"Ada","comma, stays",001\n"Zoë","first line\nsecond line",true\n'.encode())
    config = _config(
        test_path, "rows: !include-table rows.csv\nitems: [before, !spread [!include-table rows.csv], after]\n"
    )

    rows = [
        ["name", "note", "value"],
        ["Ada", "comma, stays", "001"],
        ["Zoë", "first line\nsecond line", "true"],
    ]
    assert load_yaml_file(config) == {
        "rows": rows,
        "items": ["before", *rows, "after"],
    }


def test_csv_preserves_crlf_inside_quoted_fields(test_path: Path) -> None:
    source = test_path / "windows-lines.csv"
    source.write_bytes(b'name,note\r\nGrace,"first line\r\nsecond line"\r\n')
    config = _config(test_path, "rows: !include-table windows-lines.csv\n")

    assert load_yaml_file(config) == {"rows": [["name", "note"], ["Grace", "first line\r\nsecond line"]]}


def test_csv_keeps_the_first_row_as_data_and_cells_as_strings(test_path: Path) -> None:
    source = test_path / "people.csv"
    source.write_text('name, note ,active\n"Ada Lovelace","hello, world",true\n', encoding="utf-8")
    config = _config(test_path, "records: !include-table people.csv\n")

    assert load_yaml_file(config) == {
        "records": [["name", " note ", "active"], ["Ada Lovelace", "hello, world", "true"]]
    }


def test_include_table_constructor_is_available_in_custom_safe_loader(test_path: Path) -> None:
    source = test_path / "rows.csv"
    source.write_text("id\n001\n", encoding="utf-8")
    config = _config(test_path, "rows: !include-table rows.csv\n")

    class CustomSafeLoader(yaml.SafeLoader):
        pass

    assert load_yaml_file(config, yaml_loader=CustomSafeLoader) == {"rows": [["id"], ["001"]]}


def test_csv_empty_input_returns_no_rows_and_header_only_is_data(test_path: Path) -> None:
    (test_path / "empty.csv").write_bytes(b"")
    (test_path / "header.csv").write_text("name,value\n", encoding="utf-8")
    config = _config(test_path, "empty: !include-table empty.csv\nheader: !include-table header.csv\n")

    assert load_yaml_file(config) == {"empty": [], "header": [["name", "value"]]}


def test_csv_preserves_blank_rows_and_uses_comma_without_sniffing(test_path: Path) -> None:
    source = test_path / "rows.csv"
    source.write_text("first;second\n\n3;4\n", encoding="utf-8")
    config = _config(test_path, "rows: !include-table rows.csv\n")

    assert load_yaml_file(config) == {"rows": [["first;second"], [], ["3;4"]]}


def test_csv_keeps_ragged_rows_and_duplicate_or_empty_first_row_cells(test_path: Path) -> None:
    source = test_path / "irregular.csv"
    source.write_text(",name,name\nshort\nwide,with,extra\n", encoding="utf-8")
    config = _config(test_path, "rows: !include-table irregular.csv\n")

    assert load_yaml_file(config) == {"rows": [["", "name", "name"], ["short"], ["wide", "with", "extra"]]}


def test_csv_syntax_errors_report_physical_line_and_preserve_cause(test_path: Path) -> None:
    source = test_path / "malformed.csv"
    source.write_text('name,note\nAda,"unterminated\nfield', encoding="utf-8")
    config = _config(test_path, "rows: !include-table malformed.csv\n")

    with pytest.raises(ConstructorError, match=r"CSV parse error at line 3") as caught:
        load_yaml_file(config)

    assert str(source) in str(caught.value)
    assert caught.value.problem_mark is not None
    assert isinstance(caught.value.__cause__, FormatParseError)
    assert isinstance(caught.value.__cause__.__cause__, csv.Error)


def test_csv_strict_reader_accepts_quote_inside_unquoted_field_as_stdlib_behavior(test_path: Path) -> None:
    source = test_path / "quote-in-unquoted.csv"
    source.write_text('left,right\na,b"c\n', encoding="utf-8")
    config = _config(test_path, "rows: !include-table quote-in-unquoted.csv\n")

    assert load_yaml_file(config) == {"rows": [["left", "right"], ["a", 'b"c']]}


def test_csv_strict_reader_rejects_text_after_closing_quote(test_path: Path) -> None:
    source = test_path / "text-after-quote.csv"
    source.write_text('name,note\nAda,"closed"junk\n', encoding="utf-8")
    config = _config(test_path, "rows: !include-table text-after-quote.csv\n")

    with pytest.raises(ConstructorError, match=r"CSV parse error at line 2") as caught:
        load_yaml_file(config)

    assert str(source) in str(caught.value)
    assert isinstance(caught.value.__cause__, FormatParseError)
    assert isinstance(caught.value.__cause__.__cause__, csv.Error)


def test_csv_field_limit_uses_decoded_characters_and_does_not_change_global_setting(test_path: Path) -> None:
    field_limit = csv.field_size_limit()
    assert field_limit > 0

    source = test_path / "unicode-field.csv"
    field = "é" * field_limit
    contents = (field + "\n").encode("utf-8")
    assert len(contents) > field_limit
    source.write_bytes(contents)
    config = _config(test_path, "rows: !include-table unicode-field.csv\n")

    result = load_yaml_file(
        config,
        max_file_bytes=len(contents),
        max_total_include_bytes=len(contents),
    )

    assert result == {"rows": [[field]]}
    assert csv.field_size_limit() == field_limit


def test_csv_field_limit_rejects_one_over_runtime_limit_without_changing_it(test_path: Path) -> None:
    field_limit = csv.field_size_limit()
    assert field_limit > 0

    source = test_path / "oversized-field.csv"
    contents = ("x" * (field_limit + 1) + "\n").encode("ascii")
    source.write_bytes(contents)
    config = _config(test_path, "rows: !include-table oversized-field.csv\n")

    with pytest.raises(ConstructorError, match="field larger than field limit") as caught:
        load_yaml_file(
            config,
            max_file_bytes=len(contents),
            max_total_include_bytes=len(contents),
        )

    assert str(source) in str(caught.value)
    assert isinstance(caught.value.__cause__, FormatParseError)
    assert isinstance(caught.value.__cause__.__cause__, csv.Error)
    assert csv.field_size_limit() == field_limit


def test_concurrent_csv_loads_leave_runtime_field_limit_unchanged(test_path: Path) -> None:
    field_limit = csv.field_size_limit()
    configs: list[Path] = []
    for name, value in (("first", "001"), ("second", "3.5")):
        source_root = test_path / name
        source_root.mkdir()
        (source_root / f"{name}.csv").write_text(f"value\n{value}\n", encoding="utf-8")
        configs.append(_config(source_root, f"rows: !include-table {name}.csv\n"))

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = (future.result() for future in (executor.submit(load_yaml_file, config) for config in configs))

    assert first == {"rows": [["value"], ["001"]]}
    assert second == {"rows": [["value"], ["3.5"]]}
    assert csv.field_size_limit() == field_limit


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        (b"\xef\xbb\xbfname,value\n", "byte-order mark"),
        (b"name,value\nAda,\xff\n", "invalid byte at offset 15"),
    ],
)
def test_csv_rejects_bom_and_invalid_utf8_with_source_context(test_path: Path, contents: bytes, message: str) -> None:
    source = test_path / "encoding.csv"
    source.write_bytes(contents)
    config = _config(test_path, "rows: !include-table encoding.csv\n")

    with pytest.raises(ConstructorError, match=message) as caught:
        load_yaml_file(config)

    assert str(source) in str(caught.value)
    assert caught.value.problem_mark is not None
    assert isinstance(caught.value.__cause__, FormatParseError)


def test_csv_include_uses_resolved_source_and_shared_budgets_and_closes_stream(test_path: Path) -> None:
    actual = test_path / "actual.csv"
    contents = b"name,value\nAda,001\n"
    actual.write_bytes(contents)
    config = _config(test_path, "rows: !include-table alias.csv\n")
    stream = BytesIO(contents)

    class RedirectResolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            return ResolvedInclude(FileSource(actual), lambda: stream)

    assert load_yaml_file(
        config,
        include_resolver=RedirectResolver(),
        max_file_bytes=len(contents),
        max_total_include_bytes=len(contents),
        max_include_files=1,
    ) == {"rows": [["name", "value"], ["Ada", "001"]]}
    assert stream.closed

    rejected_stream = BytesIO(contents)

    class LimitedResolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            return ResolvedInclude(FileSource(actual), lambda: rejected_stream)

    with pytest.raises(yaml.YAMLError, match="per-file size limit"):
        load_yaml_file(
            config,
            include_resolver=LimitedResolver(),
            max_file_bytes=len(contents) - 1,
        )
    assert rejected_stream.closed


def test_configured_loader_uses_the_same_simple_csv_row_shape(test_path: Path) -> None:
    (test_path / "people.csv").write_text("name\nAda\n", encoding="utf-8")
    config = _config(test_path, "rows: !include-table people.csv\n")
    default_loader = YamlConfigLoader()
    configured_loader = YamlConfigLoader(format_registry=FormatRegistry())

    expected = {"rows": [["name"], ["Ada"]]}
    assert default_loader.load_file(config) == expected
    assert configured_loader.load_file(config) == expected
