"""Expected outputs for examples/; see examples/README.md and update both together."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from examples.custom_loader import load_example as load_custom_loader_example
from examples.in_memory_config import load_example
from examples.include_formats import load_example as load_include_formats_example
from examples.include_limits import load_example as load_unbounded_example
from examples.include_search import load_example as load_search_example
from examples.load_config import load_examples
from yaml_compose import IncludeSearch, load_yaml_file


@pytest.fixture
def expected_production_config() -> dict[str, object]:
    return {
        "service": {
            "host": "api.us.example.test",
            "port": 8081,
            "labels": ["production"],
            "clearable": [],
            "token": None,
            "nested": {"retries": 3, "timeout_seconds": 45},
        },
        "logging": {"level": "warning", "format": "json"},
        "backend": "remote",
    }


@pytest.fixture
def expected_simple_examples() -> dict[str, object]:
    return {
        "quickstart": {
            "service": {"host": "api.example.test", "port": 8080, "labels": ["production"]},
            "steps": ["prepare", "lint", "test", "deploy"],
        },
        "include_value": {"database": {"host": "db.example.test", "port": 5432}},
        "shallow_merge": {"service": {"port": 8081}, "enabled": True},
        "deep_merge": {"service": {"host": "localhost", "port": 8081}, "enabled": True},
        "merge_precedence": {
            "first": {"winner": "first", "service": {"host": "localhost", "port": 8080}},
            "second": {"winner": "second", "service": {"port": 9000, "timeout": 30}},
            "merged": {
                "winner": "first",
                "service": {"host": "localhost", "port": 9090, "timeout": 30},
            },
        },
        "spread_sources": {
            "shared": ["alpha", "beta"],
            "items": ["before", "alpha", "beta", "remote-a", "remote-b", "after"],
        },
        "replace_mapping": {"service": {"port": 9000}, "enabled": True},
    }


@pytest.mark.parametrize(
    ("name", "filename"),
    [
        ("quickstart", "quickstart/production.yaml"),
        ("include_value", "include_value.yaml"),
        ("shallow_merge", "shallow_merge.yaml"),
        ("deep_merge", "deep_merge.yaml"),
        ("merge_precedence", "merge_precedence.yaml"),
        ("spread_sources", "spread_sources.yaml"),
        ("replace_mapping", "replace_mapping.yaml"),
    ],
)
def test_simple_example_file_has_the_documented_result(
    name: str, filename: str, examples_path: Path, expected_simple_examples: dict[str, object]
) -> None:
    assert load_yaml_file(examples_path / filename) == expected_simple_examples[name]


def test_simple_runner_loads_all_the_documented_examples(
    expected_simple_examples: dict[str, object],
) -> None:
    assert load_examples() == expected_simple_examples


def test_readme_and_examples_index_link_to_their_tests(repo_root: Path, examples_path: Path) -> None:
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    examples = (examples_path / "README.md").read_text(encoding="utf-8")
    assert "[examples index](examples/README.md)" in readme
    assert "tests/test_examples.py" in readme
    assert "../tests/test_examples.py" in examples
    assert "../tests/test_semantics.py" in examples


def test_filesystem_example_resolves_all_layers_and_overrides(
    examples_path: Path,
    expected_production_config: dict[str, object],
) -> None:
    result = load_yaml_file(examples_path / "production.yaml")
    assert result == expected_production_config


def test_in_memory_example_resolves_nested_relative_include() -> None:
    assert load_example() == {"service": {"host": "localhost", "port": 8081, "labels": ["base"]}}


def test_json_and_toml_example_has_the_documented_result() -> None:
    assert load_include_formats_example() == {
        "service": {"host": "api.example.test", "port": 8080, "labels": ["production"]},
        "logging": {"level": "info", "format": "json"},
        "database": {
            "host": "db.example.test",
            "port": 5432,
            "created": datetime(1979, 5, 27, 7, 32, tzinfo=timezone.utc),
        },
        "steps": ["prepare", "lint", "test", "deploy"],
    }


def test_record_formats_example_has_the_documented_result(examples_path: Path) -> None:
    assert load_yaml_file(examples_path / "include_records.yaml") == {
        "records": [{"event": "started", "service": "api"}, None],
        "steps": ["prepare", "lint", {"suite": "unit", "passed": True}, "deploy"],
    }


def test_custom_loader_example_uses_its_tag_inside_a_nested_include() -> None:
    assert load_custom_loader_example() == {"profile": {"name": "ALICE"}}


@pytest.mark.parametrize("policy", ["local-first", "search-first", "local-only", "search-only"])
def test_search_example_preserves_explicit_relative_files_and_local_overrides(
    policy: IncludeSearch,
) -> None:
    local = policy in ("local-first", "local-only")
    assert load_search_example(policy) == {
        "service": {
            "host": "local.example.test" if local else "shared.example.test",
            "port": 9090,
            "limits": {"requests": 100, "burst": 20} if local else {"requests": 200, "burst": 40},
            "steps": ["prepare", "local-check" if local else "shared-check", "package"],
            "pinned": "local-identity",
        }
    }


def test_include_limit_example_distinguishes_bounded_from_unbounded(
    examples_path: Path,
    expected_production_config: dict[str, object],
) -> None:
    production = examples_path / "production.yaml"
    with pytest.raises(yaml.YAMLError, match="maximum include count"):
        load_yaml_file(production, max_include_files=1)
    assert load_unbounded_example() == expected_production_config


def test_merge_modes_example_covers_sources_depth_and_replacement(examples_path: Path) -> None:
    result = load_yaml_file(examples_path / "merge_modes.yaml")
    assert isinstance(result, dict)
    assert result.keys() == {
        "defaults",
        "replacement",
        "shallow_anchor",
        "shallow_file",
        "deep_anchor",
        "deep_file",
        "shallow_sources",
        "deep_sources",
        "replace_inline",
        "replace_anchor",
        "replace_file",
        "clear_mapping",
        "shallow_delete",
        "deep_delete",
    }
    assert result["defaults"] == {
        "service": {"host": "localhost", "port": 8080},
        "enabled": True,
    }
    assert result["replacement"] == {"port": 9000}
    shallow = {"service": {"port": 8081}, "enabled": True}
    deep = {"service": {"host": "localhost", "port": 8081}, "enabled": True}
    assert result["shallow_anchor"] == shallow
    assert result["shallow_file"] == shallow
    assert result["deep_anchor"] == deep
    assert result["deep_file"] == deep
    assert result["shallow_sources"] == {
        "service": {"port": 8082},
        "enabled": True,
        "region": "us",
    }
    assert result["deep_sources"] == {
        "service": {"host": "localhost", "port": 8082, "timeout": 30},
        "enabled": True,
        "region": "us",
    }
    replaced = {"service": {"port": 9000}, "enabled": True}
    assert result["replace_inline"] == replaced
    assert result["replace_anchor"] == replaced
    assert result["replace_file"] == replaced
    assert result["clear_mapping"] == {"service": {}, "enabled": True}
    assert result["shallow_delete"] == {"service": {"host": "localhost", "port": 8080}}
    assert result["deep_delete"] == {
        "service": {"host": "localhost"},
        "enabled": True,
    }
