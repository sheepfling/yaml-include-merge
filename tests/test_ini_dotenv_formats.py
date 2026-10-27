"""INI and dotenv adapters use the bounded include registry without guessing."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from yaml.constructor import ConstructorError

from yaml_compose import (
    FileSource,
    FormatParseError,
    FormatRegistry,
    IniAdapter,
    YamlConfigLoader,
    load_yaml_file,
)


def _config(path: Path, body: str) -> Path:
    config = path / "main.yaml"
    config.write_text(f"%LOADER YamlCompose\n---\n{body}", encoding="utf-8")
    return config


def test_ini_preserves_key_case_string_values_and_default_inheritance(test_path: Path) -> None:
    source = test_path / "settings.ini"
    source.write_text(
        "[DEFAULT]\nCaseSensitive = 001\nTemplate = ${literal}\n[Service]\nPort = 08080\nCaseSensitive = local\n",
        encoding="utf-8",
    )
    config = _config(test_path, "settings: !include settings.ini\n")

    assert load_yaml_file(config) == {
        "settings": {
            "DEFAULT": {"CaseSensitive": "001", "Template": "${literal}"},
            "Service": {"CaseSensitive": "local", "Template": "${literal}", "Port": "08080"},
        }
    }


def test_empty_ini_and_dotenv_sources_return_empty_mappings(test_path: Path) -> None:
    (test_path / "empty.ini").write_bytes(b"")
    (test_path / ".env").write_bytes(b"")
    config = _config(test_path, "ini: !include empty.ini\ndotenv: !include .env\n")

    assert load_yaml_file(config) == {"ini": {}, "dotenv": {}}


def test_ini_option_names_are_case_sensitive(test_path: Path) -> None:
    source = test_path / "case.ini"
    source.write_text("[Section]\nKey = first\nkey = second\n", encoding="utf-8")
    config = _config(test_path, "settings: !include case.ini\n")

    assert load_yaml_file(config) == {"settings": {"Section": {"Key": "first", "key": "second"}}}


def test_ini_conf_and_cfg_are_explicit_opt_in_aliases(test_path: Path) -> None:
    source = test_path / "settings.conf"
    source.write_text("[Service]\nName = api\n", encoding="utf-8")
    assert FormatRegistry().detect_format(FileSource(source)).format_id == "yaml"

    registry = FormatRegistry()
    registry.register(IniAdapter(), replace=True, suffixes=(".ini", ".conf", ".cfg"))
    result = YamlConfigLoader(format_registry=registry).load_file(
        _config(test_path, "settings: !include settings.conf\n")
    )
    assert result == {"settings": {"Service": {"Name": "api"}}}
    assert registry.detect_format(FileSource(test_path / "settings.cfg")).format_id == "ini"


@pytest.mark.parametrize(
    "contents",
    [
        "[service]\nKey = one\nKey = two\n",
        "[service]\nKey = one\n[service]\nOther = two\n",
        "Key = value\n",
    ],
)
def test_ini_rejects_duplicate_or_unsectioned_content_with_include_context(test_path: Path, contents: str) -> None:
    source = test_path / "invalid.ini"
    source.write_text(contents, encoding="utf-8")
    config = _config(test_path, "settings: !include invalid.ini\n")

    with pytest.raises(ConstructorError) as caught:
        load_yaml_file(config)

    assert str(source) in str(caught.value)
    assert "while resolving !include" in str(caught.value)
    assert isinstance(caught.value.__cause__, FormatParseError)
    assert caught.value.problem_mark is not None


def test_dotenv_exact_env_basename_is_detected_without_environment_mutation(test_path: Path) -> None:
    source = test_path / ".env"
    source.write_text(
        '# comment\nexport API_KEY = "secret # value" # trailing comment\n'
        "PLAIN=001\nTEMPLATE='${HOME}'\nHASH=value # literal\nESCAPED=\"a\\nb\"\nEMPTY=\n"
        "YAML_COMPOSE_DOTENV_ADAPTER_PROBE=should-not-set\n",
        encoding="utf-8",
    )
    config = _config(test_path, "settings: !include .env\n")
    environment_key = "YAML_COMPOSE_DOTENV_ADAPTER_PROBE"
    existed = environment_key in os.environ
    previous = os.environ.get(environment_key)

    assert load_yaml_file(config) == {
        "settings": {
            "API_KEY": "secret # value",
            "PLAIN": "001",
            "TEMPLATE": "${HOME}",
            "HASH": "value # literal",
            "ESCAPED": "a\\nb",
            "EMPTY": "",
            environment_key: "should-not-set",
        }
    }
    assert (environment_key in os.environ) == existed
    assert os.environ.get(environment_key) == previous


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("A=one\nA=two\n", "duplicate key 'A'"),
        ("not an assignment\n", "expected KEY=value"),
        ('A="unfinished\n', "unterminated quoted value"),
        ("A=ok\nB='value' trailing\n", "unexpected text after quoted value"),
    ],
)
def test_dotenv_reports_invalid_lines_without_evaluating_shell_syntax(
    test_path: Path, contents: str, message: str
) -> None:
    source = test_path / "values.dotenv"
    source.write_text(contents, encoding="utf-8")
    config = _config(test_path, "settings: !include values.dotenv\n")

    with pytest.raises(ConstructorError, match=message) as caught:
        load_yaml_file(config)

    assert str(source) in str(caught.value)
    assert isinstance(caught.value.__cause__, FormatParseError)
    assert caught.value.problem_mark is not None


@pytest.mark.parametrize("format_id", ["ini", "dotenv"])
def test_config_adapters_reject_invalid_utf8_and_bom(test_path: Path, format_id: str) -> None:
    source = test_path / ("values.ini" if format_id == "ini" else ".env")
    source.write_bytes(b"\xef\xbb\xbf" + b"[section]\nkey=value\n")
    config = _config(test_path, f"settings: !include {source.name}\n")

    with pytest.raises(ConstructorError, match=r"BOM\) is not supported"):
        load_yaml_file(config)

    source.write_bytes(b"key=\xff")
    with pytest.raises(ConstructorError, match="not UTF-8 text"):
        load_yaml_file(config)


def test_dotenv_adapter_is_registered_for_env_basename_and_dotenv_suffix(test_path: Path) -> None:
    registry = FormatRegistry()
    assert registry.detect_format(FileSource(test_path / ".env")).format_id == "dotenv"
    assert registry.detect_format(FileSource(test_path / ".ENV")).format_id == "dotenv"
    assert registry.detect_format(FileSource(test_path / "settings.dotenv")).format_id == "dotenv"
    assert registry.detect_format(FileSource(test_path / "service.env")).format_id == "yaml"


def test_registry_alias_collision_rolls_back_atomically(test_path: Path) -> None:
    registry = FormatRegistry()
    with pytest.raises(ValueError, match=r"filename '\.env' is already owned"):
        registry.register(IniAdapter(), replace=True, filenames=(".env",))

    assert registry.get_format("ini").suffixes == (".ini",)
    assert registry.detect_format(FileSource(test_path / "settings.ini")).format_id == "ini"
    assert registry.detect_format(FileSource(test_path / ".env")).format_id == "dotenv"


def test_unregister_releases_exact_filename_alias() -> None:
    registry = FormatRegistry()
    registry.unregister("dotenv")
    registry.register(IniAdapter(), replace=True, filenames=(".env",))

    assert registry.detect_format(FileSource(Path(".env"))).format_id == "ini"
