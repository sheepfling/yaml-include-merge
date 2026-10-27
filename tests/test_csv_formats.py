"""CSV includes use an explicit row shape through the shared adapter registry."""

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


def _dict_registry() -> FormatRegistry:
    registry = FormatRegistry()
    registry.register(CsvAdapter(mode="dicts"), replace=True)
    return registry


def test_csv_is_a_registered_record_format_with_row_lists_by_default(test_path: Path) -> None:
    source = test_path / "rows.csv"
    source.write_text("id,enabled\n001,true\n", encoding="utf-8")
    config = _config(test_path, "rows: !include rows.csv\n")
    descriptor = FormatRegistry().detect_format(FileSource(source))

    assert descriptor.format_id == "csv"
    assert descriptor.capability == "records"
    assert load_yaml_file(config) == {"rows": [["id", "enabled"], ["001", "true"]]}


def test_csv_row_lists_preserve_quoting_newlines_unicode_and_strings(test_path: Path) -> None:
    source = test_path / "rows.csv"
    source.write_text(
        'name,note,value\n"Ada","comma, stays",001\n"Zoë","first line\nsecond line",true\n',
        encoding="utf-8",
    )
    config = _config(test_path, "rows: !include rows.csv\nitems: [before, !spread [!include rows.csv], after]\n")

    rows = [
        ["name", "note", "value"],
        ["Ada", "comma, stays", "001"],
        ["Zoë", "first line\nsecond line", "true"],
    ]
    assert load_yaml_file(config) == {
        "rows": rows,
        "items": ["before", *rows, "after"],
    }


def test_csv_header_records_are_an_explicit_per_loader_mode(test_path: Path) -> None:
    source = test_path / "people.csv"
    source.write_text('name, note ,active\n"Ada Lovelace","hello, world",true\n', encoding="utf-8")
    config = _config(test_path, "records: !include people.csv\n")

    assert load_yaml_file(config) == {
        "records": [["name", " note ", "active"], ["Ada Lovelace", "hello, world", "true"]]
    }
    assert load_yaml_file(config, format_registry=_dict_registry()) == {
        "records": [{"name": "Ada Lovelace", " note ": "hello, world", "active": "true"}]
    }


@pytest.mark.parametrize("mode", ["rows", "dicts"])
def test_csv_empty_input_returns_no_records_and_header_only_returns_empty_data(test_path: Path, mode: str) -> None:
    (test_path / "empty.csv").write_bytes(b"")
    (test_path / "header.csv").write_text("name,value\n", encoding="utf-8")
    config = _config(test_path, "empty: !include empty.csv\nheader: !include header.csv\n")
    registry = FormatRegistry()
    if mode == "dicts":
        registry.register(CsvAdapter(mode="dicts"), replace=True)

    expected_header = [] if mode == "dicts" else [["name", "value"]]
    assert load_yaml_file(config, format_registry=registry) == {"empty": [], "header": expected_header}


def test_csv_row_mode_preserves_blank_rows_and_uses_comma_without_sniffing(test_path: Path) -> None:
    source = test_path / "rows.csv"
    source.write_text("first;second\n\n3;4\n", encoding="utf-8")
    config = _config(test_path, "rows: !include rows.csv\n")

    assert load_yaml_file(config) == {"rows": [["first;second"], [], ["3;4"]]}


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("name,name\nAda,Lovelace\n", "CSV header at line 1, column 2 duplicates 'name'"),
        ("name,,active\nAda,Byron,true\n", "CSV header at line 1, column 2 is empty"),
        ("\nname\nAda\n", "CSV header row at line 1 is empty"),
    ],
)
def test_csv_dict_mode_rejects_empty_or_duplicate_headers_with_location(
    test_path: Path, contents: str, message: str
) -> None:
    source = test_path / "bad.csv"
    source.write_text(contents, encoding="utf-8")
    config = _config(test_path, "rows: !include bad.csv\n")

    with pytest.raises(ConstructorError, match=message) as caught:
        load_yaml_file(config, format_registry=_dict_registry())

    assert str(source) in str(caught.value)
    assert caught.value.problem_mark is not None
    assert isinstance(caught.value.__cause__, FormatParseError)


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("id,name\n1\n", "CSV record ending at line 2 has 1 columns; expected 2"),
        ("id,name\n1,Ada,extra\n", "CSV record ending at line 2 has 3 columns; expected 2"),
        ('id,name\n1,"two\nlines",extra\n', "CSV record ending at line 3 has 3 columns; expected 2"),
    ],
)
def test_csv_dict_mode_rejects_ragged_rows_with_physical_line(test_path: Path, contents: str, message: str) -> None:
    source = test_path / "ragged.csv"
    source.write_text(contents, encoding="utf-8")
    config = _config(test_path, "rows: !include ragged.csv\n")

    with pytest.raises(ConstructorError, match=message) as caught:
        load_yaml_file(config, format_registry=_dict_registry())

    assert str(source) in str(caught.value)
    assert caught.value.problem_mark is not None
    assert isinstance(caught.value.__cause__, FormatParseError)


def test_csv_syntax_errors_report_physical_line_and_preserve_cause(test_path: Path) -> None:
    source = test_path / "malformed.csv"
    source.write_text('name,note\nAda,"unterminated\nfield', encoding="utf-8")
    config = _config(test_path, "rows: !include malformed.csv\n")

    with pytest.raises(ConstructorError, match=r"CSV parse error at line 3") as caught:
        load_yaml_file(config)

    assert str(source) in str(caught.value)
    assert caught.value.problem_mark is not None
    assert isinstance(caught.value.__cause__, FormatParseError)
    assert isinstance(caught.value.__cause__.__cause__, csv.Error)


def test_csv_strict_reader_accepts_quote_inside_unquoted_field_as_stdlib_behavior(test_path: Path) -> None:
    source = test_path / "quote-in-unquoted.csv"
    source.write_text('left,right\na,b"c\n', encoding="utf-8")
    config = _config(test_path, "rows: !include quote-in-unquoted.csv\n")

    assert load_yaml_file(config) == {"rows": [["left", "right"], ["a", 'b"c']]}


def test_csv_strict_reader_rejects_text_after_closing_quote(test_path: Path) -> None:
    source = test_path / "text-after-quote.csv"
    source.write_text('name,note\nAda,"closed"junk\n', encoding="utf-8")
    config = _config(test_path, "rows: !include text-after-quote.csv\n")

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
    config = _config(test_path, "rows: !include unicode-field.csv\n")

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
    config = _config(test_path, "rows: !include oversized-field.csv\n")

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
        configs.append(_config(source_root, f"rows: !include {name}.csv\n"))

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
    config = _config(test_path, "rows: !include encoding.csv\n")

    with pytest.raises(ConstructorError, match=message) as caught:
        load_yaml_file(config)

    assert str(source) in str(caught.value)
    assert caught.value.problem_mark is not None
    assert isinstance(caught.value.__cause__, FormatParseError)


def test_csv_mode_validation_is_explicit() -> None:
    with pytest.raises(ValueError, match="CSV mode must be 'rows' or 'dicts'"):
        CsvAdapter(mode="sniff")  # type: ignore[arg-type]


def test_csv_include_uses_resolved_source_and_shared_budgets_and_closes_stream(test_path: Path) -> None:
    actual = test_path / "actual.csv"
    contents = b"name,value\nAda,001\n"
    actual.write_bytes(contents)
    config = _config(test_path, "rows: !include alias.csv\n")
    stream = BytesIO(contents)

    class RedirectResolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            return ResolvedInclude(FileSource(actual), lambda: stream)

    assert load_yaml_file(
        config,
        include_resolver=RedirectResolver(),
        format_registry=_dict_registry(),
        max_file_bytes=len(contents),
        max_total_include_bytes=len(contents),
        max_include_files=1,
    ) == {"rows": [{"name": "Ada", "value": "001"}]}
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


def test_configured_loader_keeps_csv_modes_isolated(test_path: Path) -> None:
    (test_path / "people.csv").write_text("name\nAda\n", encoding="utf-8")
    config = _config(test_path, "rows: !include people.csv\n")
    rows = YamlConfigLoader()
    dicts = YamlConfigLoader(format_registry=_dict_registry())

    assert rows.load_file(config) == {"rows": [["name"], ["Ada"]]}
    assert dicts.load_file(config) == {"rows": [{"name": "Ada"}]}
