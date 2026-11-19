from pathlib import Path

import pytest
import yaml

from yaml_compose import Include, IncludeTable, dump, load_yaml_file


def test_dump_compose_manifest(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"

    dump({"base": Include("with # café.yaml"), "label": "with # café.yaml"}, path)
    text = path.read_text(encoding="utf-8")
    assert text.splitlines()[:2] == ["%LOADER YamlCompose", "---"]
    assert text.splitlines().count("---") == 1
    assert "base: !include 'with # café.yaml'" in text
    assert "label: 'with # café.yaml'" in text

    # Saving the manifest does not require the included file to exist.
    (tmp_path / "with # café.yaml").write_text("name: café\n", encoding="utf-8")
    assert load_yaml_file(path) == {"base": {"name": "café"}, "label": "with # café.yaml"}


@pytest.mark.parametrize("path", ["", "/base.yaml", "C:base.yaml", "C:/base.yaml", r"\base.yaml"])
@pytest.mark.parametrize("marker", [Include, IncludeTable])
def test_include_requires_relative_path(path: str, marker: type[Include] | type[IncludeTable]) -> None:
    with pytest.raises(ValueError, match="relative path"):
        marker(path)


def test_failed_dump_preserves_existing_destination(tmp_path: Path) -> None:
    path = tmp_path / "original.yaml"
    path.write_bytes(b"original\n")

    with pytest.raises(yaml.representer.RepresenterError):
        dump({"unsupported": object()}, path)

    assert path.read_bytes() == b"original\n"


@pytest.mark.parametrize("value", [Include("base.yaml"), IncludeTable("data.csv")])
def test_safe_dumper_is_unchanged(value: Include | IncludeTable) -> None:
    with pytest.raises(yaml.representer.RepresenterError):
        yaml.safe_dump(value)


def test_scalar_root_keeps_separate_document_start(tmp_path: Path) -> None:
    path = tmp_path / "scalar.yaml"

    dump("plain text", str(path))

    assert path.read_text(encoding="utf-8").splitlines()[:3] == [
        "%LOADER YamlCompose",
        "---",
        "plain text",
    ]
    assert load_yaml_file(path) == "plain text"


def test_dump_mixed_json_and_csv_includes(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"
    csv_name = "data # café.csv"

    dump({"settings": Include("settings.json"), "rows": IncludeTable(csv_name), "label": csv_name}, path)
    assert path.read_text(encoding="utf-8").splitlines() == [
        "%LOADER YamlCompose",
        "---",
        "settings: !include 'settings.json'",
        "rows: !include-table 'data # café.csv'",
        "label: 'data # café.csv'",
    ]

    # Includes need not exist until the generated manifest is loaded.
    (tmp_path / "settings.json").write_text('{"active": true}', encoding="utf-8")
    (tmp_path / csv_name).write_text("name,count\nrow,02\n", encoding="utf-8")
    assert load_yaml_file(path) == {
        "settings": {"active": True},
        "rows": [["name", "count"], ["row", "02"]],
        "label": csv_name,
    }
