from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from yaml_compose import load_yaml_file


def write_yaml(root: Path, name: str, contents: str) -> Path:
    path = root / name
    path.write_text(contents, encoding="utf-8")
    return path


def test_include_loads_a_file_as_a_value(test_path: Path) -> None:
    data = {"host": "localhost", "port": 5432}
    write_yaml(test_path, "database.yaml", yaml.dump(data))
    config = write_yaml(
        test_path,
        "config.yaml",
        "%LOADER YamlCompose\n---\ndatabase: !include database.yaml\n",
    )

    assert load_yaml_file(config) == {"database": data}


def test_mapping_overlay_injects_and_deep_merges_a_config(test_path: Path) -> None:
    write_yaml(test_path, "defaults.yaml", "service:\n  host: localhost\n  port: 8080\nretries: 3\n")
    config = write_yaml(
        test_path,
        "production.yaml",
        "%LOADER YamlCompose\n---\n<<<: !include defaults.yaml\nservice:\n  host: app.example.com\n",
    )

    assert load_yaml_file(config) == {
        "service": {"host": "app.example.com", "port": 8080},
        "retries": 3,
    }


def test_shallow_and_deep_merges_accept_anchors_and_files(test_path: Path) -> None:
    write_yaml(test_path, "defaults.yaml", "service: {host: localhost, port: 8080}\nenabled: true\n")
    config = write_yaml(
        test_path,
        "config.yaml",
        "%LOADER YamlCompose\n---\n"
        "defaults: &defaults {service: {host: localhost, port: 8080}, enabled: true}\n"
        "shallow_anchor: {<<: *defaults, service: {port: 8081}}\n"
        "deep_anchor: {<<<: *defaults, service: {port: 8081}}\n"
        "shallow_file: {<<: !include defaults.yaml, service: {port: 8081}}\n"
        "deep_file: {<<<: !include defaults.yaml, service: {port: 8081}}\n",
    )

    result = load_yaml_file(config)
    assert isinstance(result, dict)
    shallow = {"service": {"port": 8081}, "enabled": True}
    deep = {"service": {"host": "localhost", "port": 8081}, "enabled": True}
    assert result["shallow_anchor"] == shallow
    assert result["shallow_file"] == shallow
    assert result["deep_anchor"] == deep
    assert result["deep_file"] == deep


def test_merge_source_list_gives_earlier_sources_precedence(test_path: Path) -> None:
    write_yaml(
        test_path,
        "later.yaml",
        "service: {port: 9090, timeout: 30}\nflavor: later\n",
    )
    config = write_yaml(
        test_path,
        "config.yaml",
        "%LOADER YamlCompose\n---\n"
        "first: &first {service: {host: localhost, port: 8080}, flavor: first}\n"
        "shallow: {<<: [*first, !include later.yaml]}\n"
        "deep: {<<<: [*first, !include later.yaml]}\n"
        "local: {flavor: local, service: {port: 9999}, "
        "<<<: [*first, !include later.yaml]}\n",
    )

    result = load_yaml_file(config)
    assert isinstance(result, dict)
    assert result["shallow"] == {
        "service": {"host": "localhost", "port": 8080},
        "flavor": "first",
    }
    assert result["deep"] == {
        "service": {"host": "localhost", "port": 8080, "timeout": 30},
        "flavor": "first",
    }
    assert result["local"] == {
        "service": {"host": "localhost", "port": 9999, "timeout": 30},
        "flavor": "local",
    }


def test_ordinary_yaml_merge_list_keeps_pyyaml_precedence(test_path: Path) -> None:
    config = write_yaml(
        test_path,
        "ordinary.yaml",
        "first: &first {flavor: first}\nlater: &later {flavor: later}\nmerged: {<<: [*first, *later]}\n",
    )

    assert load_yaml_file(config) == {
        "first": {"flavor": "first"},
        "later": {"flavor": "later"},
        "merged": {"flavor": "first"},
    }


def test_replace_overrides_one_mapping_in_a_deep_merge(test_path: Path) -> None:
    write_yaml(test_path, "replacement.yaml", "port: 8081\n")
    config = write_yaml(
        test_path,
        "config.yaml",
        "%LOADER YamlCompose\n---\n"
        "defaults: &defaults {service: {host: localhost, port: 8080}, enabled: true}\n"
        "replacement: &replacement {port: 8081}\n"
        "inline: {<<<: *defaults, service: !replace {port: 8081}}\n"
        "anchor: {<<<: *defaults, service: !replace [*replacement]}\n"
        "file: {<<<: *defaults, service: !replace [!include replacement.yaml]}\n"
        "clear: {<<<: *defaults, service: !replace {}}\n"
        "new: {<<<: *defaults, extra: !replace {flag: true}}\n",
    )

    result = load_yaml_file(config)
    assert isinstance(result, dict)
    expected = {"service": {"port": 8081}, "enabled": True}
    assert result["inline"] == expected
    assert result["anchor"] == expected
    assert result["file"] == expected
    assert result["clear"] == {"service": {}, "enabled": True}
    assert result["new"] == {
        "service": {"host": "localhost", "port": 8080},
        "enabled": True,
        "extra": {"flag": True},
    }
    assert result["defaults"] == {
        "service": {"host": "localhost", "port": 8080},
        "enabled": True,
    }


def test_delete_removes_a_key_from_an_included_config(test_path: Path) -> None:
    write_yaml(test_path, "defaults.yaml", "keep: stay\nremove: gone\n")
    config = write_yaml(
        test_path,
        "production.yaml",
        "%LOADER YamlCompose\n---\n<<<: !include defaults.yaml\nremove: !delete\n",
    )

    assert load_yaml_file(config) == {"keep": "stay"}


def test_null_keeps_a_key_while_delete_removes_it(test_path: Path) -> None:
    write_yaml(test_path, "base.yaml", "clear: old\nremove: old\nkeep: value\n")
    config = write_yaml(
        test_path,
        "config.yaml",
        "%LOADER YamlCompose\n---\n<<<: !include base.yaml\nclear: null\nremove: !delete\nmissing: !delete\n",
    )

    assert load_yaml_file(config) == {"clear": None, "keep": "value"}


def test_delete_also_removes_an_inherited_key_in_a_shallow_merge(test_path: Path) -> None:
    config = write_yaml(
        test_path,
        "config.yaml",
        "%LOADER YamlCompose\n---\nbase: &base {keep: value, remove: old}\nlocal:\n  <<: *base\n  remove: !delete\n",
    )

    assert load_yaml_file(config) == {
        "base": {"keep": "value", "remove": "old"},
        "local": {"keep": "value"},
    }


def test_delete_in_a_deep_nested_overlay_removes_only_that_key(test_path: Path) -> None:
    config = write_yaml(
        test_path,
        "config.yaml",
        "%LOADER YamlCompose\n---\n"
        "base: &base {service: {host: localhost, token: secret}}\n"
        "local:\n  <<<: *base\n  service:\n    token: !delete\n    absent: !delete\n",
    )

    assert load_yaml_file(config) == {
        "base": {"service": {"host": "localhost", "token": "secret"}},
        "local": {"service": {"host": "localhost"}},
    }


@pytest.mark.parametrize(
    "document",
    [
        "orphan: !delete",
        "items:\n  - !delete",
        "service:\n  token: !delete",
        "base: &base {service: {host: localhost, token: secret}}\nlocal:\n  <<: *base\n  service:\n    token: !delete",
    ],
)
def test_delete_requires_a_mapping_overlay_at_its_own_level(test_path: Path, document: str) -> None:
    config = write_yaml(test_path, "config.yaml", f"%LOADER YamlCompose\n---\n{document}\n")

    with pytest.raises(yaml.YAMLError, match="!delete is only valid inside a mapping overlay"):
        load_yaml_file(config)


def test_delete_cannot_be_used_as_a_mapping_key(test_path: Path) -> None:
    config = write_yaml(test_path, "config.yaml", "%LOADER YamlCompose\n---\n? !delete\n: value\n")

    with pytest.raises(yaml.YAMLError, match="cannot be used as mapping keys"):
        load_yaml_file(config)


def test_lists_replace_and_an_empty_list_clears(test_path: Path) -> None:
    write_yaml(test_path, "base.yaml", "labels: [base]\nclearable: [old]\n")
    config = write_yaml(
        test_path,
        "config.yaml",
        "%LOADER YamlCompose\n---\n<<<: !include base.yaml\nlabels: [new]\nclearable: []\n",
    )

    assert load_yaml_file(config) == {"labels": ["new"], "clearable": []}


def test_a_local_scalar_replaces_an_inherited_mapping(test_path: Path) -> None:
    write_yaml(test_path, "base.yaml", "backend: {mode: pooled}\n")
    config = write_yaml(
        test_path,
        "config.yaml",
        "%LOADER YamlCompose\n---\n<<<: !include base.yaml\nbackend: remote\n",
    )

    assert load_yaml_file(config) == {"backend": "remote"}


def test_spread_places_anchored_and_file_items_at_each_operator(test_path: Path) -> None:
    write_yaml(test_path, "extra.yaml", "- remote-a\n- remote-b\n")
    config = write_yaml(
        test_path,
        "config.yaml",
        "%LOADER YamlCompose\n---\n"
        "shared: &shared [alpha, beta]\n"
        "items:\n"
        "  - before\n"
        "  - !spread [*shared]\n"
        "  - middle\n"
        "  - !spread [!include extra.yaml]\n"
        "  - !spread [*shared]\n"
        "  - after\n",
    )

    assert load_yaml_file(config) == {
        "shared": ["alpha", "beta"],
        "items": [
            "before",
            "alpha",
            "beta",
            "middle",
            "remote-a",
            "remote-b",
            "alpha",
            "beta",
            "after",
        ],
    }


def test_spread_inserts_an_anchored_sequence_without_an_overlay(test_path: Path) -> None:
    config = write_yaml(
        test_path,
        "anchored.yaml",
        "%LOADER YamlCompose\n---\n"
        "shared: &shared [alpha, beta]\n"
        "items:\n  - before\n  - !spread [*shared]\n  - after\n",
    )

    assert load_yaml_file(config) == {
        "shared": ["alpha", "beta"],
        "items": ["before", "alpha", "beta", "after"],
    }


def test_spread_inserts_an_included_sequence_without_an_overlay(test_path: Path) -> None:
    write_yaml(test_path, "extra.yaml", "- remote-a\n- remote-b\n")
    config = write_yaml(
        test_path,
        "config.yaml",
        "%LOADER YamlCompose\n---\nitems:\n  - before\n  - !spread [!include extra.yaml]\n  - after\n",
    )

    assert load_yaml_file(config) == {"items": ["before", "remote-a", "remote-b", "after"]}


def test_spreading_an_empty_sequence_adds_no_items(test_path: Path) -> None:
    config = write_yaml(
        test_path,
        "config.yaml",
        "%LOADER YamlCompose\n---\nempty: &empty []\nitems: [before, !spread [*empty], after]\n",
    )

    assert load_yaml_file(config) == {"empty": [], "items": ["before", "after"]}
