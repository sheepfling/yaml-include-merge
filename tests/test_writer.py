from pathlib import Path

import pytest
import yaml

from yaml_compose import Include, dump, load_yaml_file


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
def test_include_requires_relative_path(path: str) -> None:
    with pytest.raises(ValueError, match="relative path"):
        Include(path)


def test_failed_dump_preserves_existing_destination(tmp_path: Path) -> None:
    path = tmp_path / "original.yaml"
    path.write_bytes(b"original\n")

    with pytest.raises(yaml.representer.RepresenterError):
        dump({"unsupported": object()}, path)

    assert path.read_bytes() == b"original\n"


def test_safe_dumper_is_unchanged() -> None:
    with pytest.raises(yaml.representer.RepresenterError):
        yaml.safe_dump(Include("base.yaml"))


def test_scalar_root_keeps_separate_document_start(tmp_path: Path) -> None:
    path = tmp_path / "scalar.yaml"

    dump("plain text", str(path))

    assert path.read_text(encoding="utf-8").splitlines()[:3] == [
        "%LOADER YamlCompose",
        "---",
        "plain text",
    ]
    assert load_yaml_file(path) == "plain text"
