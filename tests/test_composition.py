"""Interactions between nested includes, lookup policies, merges, and spreads."""

from __future__ import annotations

from collections.abc import Callable
from io import StringIO
from pathlib import Path, PurePosixPath
from textwrap import dedent

import pytest
import yaml

from yaml_compose import (
    IncludeSearch,
    MemoryIncludeResolver,
    MemoryRoot,
    MemorySource,
    YamlConfigLoader,
    YamlType,
    load_yaml_file,
)


def write_sources(root: Path, sources: dict[str, str]) -> None:
    for name, contents in sources.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(contents).lstrip("\n"), encoding="utf-8")


@pytest.fixture
def layered_sources() -> dict[str, str]:
    sources = {
        "app/main.yaml": """
            %LOADER YamlCompose
            ---
            common: &common [lint, [unit, integration], lint]
            empty: &empty []
            stage: application
            <<<: !include ./env/production.yaml
            service:
              port: 9443
              labels: [application]
              nested: {retries: 5}
            steps:
              - main-start
              - !spread [*common]
              - !spread [*empty]
              - !spread [!include steps.yaml]
              - !spread [*common]
              - main-end
        """,
        "app/env/production.yaml": """
            <<<: [!include region.yaml, !include ./fallback.yaml]
            stage: production
            service:
              token: !delete
              cleared: null
              limits: !replace [!include ./limits.yaml]
              unchanged: {}
              cleared_mapping: !replace {}
              labels: [production]
              nested: {timeout: 45}
            deployment_steps:
              - production-start
              - !spread [!include deployment_steps.yaml]
              - production-end
        """,
        "app/env/fallback.yaml": """
            flavor: fallback
            fallback_value: true
            service:
              host: fallback.example.test
              limits: {fallback: true}
              nested: {retries: 99, fallback_only: true}
        """,
        "app/env/limits.yaml": "requests: 1000\n",
        "shared/limits.yaml": "requests: 9999\n",
        "shared/fallback.yaml": "fallback_value: wrong-shared-file\n",
        "app/env/deployment_steps.yaml": "[local-deployment]\n",
        "shared/deployment_steps.yaml": "[shared-deployment]\n",
    }
    for label, directory in (("local", "app/env"), ("shared", "shared")):
        sources[f"{directory}/region.yaml"] = "<<<: !include ./defaults.yaml\nservice:\n  nested: {timeout: 40}\n"
        sources[f"{directory}/defaults.yaml"] = (
            yaml.safe_dump(
                {
                    "flavor": label,
                    "service": {
                        "host": f"{label}.example.test",
                        "port": 8080,
                        "token": "secret",
                        "cleared": "old",
                        "labels": ["base"],
                        "limits": {"requests": 100, "burst": 20},
                        "unchanged": {"flag": True},
                        "cleared_mapping": {"legacy": True},
                        "nested": {"retries": 3, "timeout": 30},
                    },
                    "deployment_steps": ["legacy"],
                }
            )
            + "database: !include ./database.yaml\n"
        )
        sources[f"{directory}/database.yaml"] = f"host: {label}.db\nport: 5432\n"
        list_directory = "app" if label == "local" else "shared"
        sources[f"{list_directory}/steps.yaml"] = (
            f"- {label}-before\n- !spread [!include ./parts.yaml]\n- {label}-after\n"
        )
        sources[f"{list_directory}/parts.yaml"] = f"- {label}-task\n- [{label}-nested]\n"
    return {name: dedent(contents).lstrip("\n") for name, contents in sources.items()}


@pytest.fixture(params=["filesystem", "memory"])
def load_layered_project(
    request: pytest.FixtureRequest, test_path: Path, layered_sources: dict[str, str]
) -> Callable[[IncludeSearch], object]:
    if request.param == "filesystem":
        write_sources(test_path, layered_sources)

        def load(policy: IncludeSearch) -> object:
            return YamlConfigLoader(search_paths=[test_path / "shared"], include_search=policy).load_file(
                test_path / "app" / "main.yaml"
            )

    else:
        root = MemoryRoot("layered-composition")
        # The memory resolver uses its namespace root as its search location.
        files = {
            name.removeprefix("shared/") if name.startswith("shared/") else name: contents
            for name, contents in layered_sources.items()
        }
        resolver = MemoryIncludeResolver(root, files)

        def load(policy: IncludeSearch) -> object:
            return YamlConfigLoader(include_resolver=resolver, allowed_roots=[root], include_search=policy).load_stream(
                StringIO(files["app/main.yaml"]),
                source=MemorySource(root, PurePosixPath("app/main.yaml")),
            )

    return load


@pytest.mark.parametrize(
    ("policy", "provider"),
    [
        ("local-first", "local"),
        ("local-only", "local"),
        ("search-first", "shared"),
        ("search-only", "shared"),
    ],
)
def test_nested_lookup_deep_overlays_and_spreads_compose_across_resolvers(
    load_layered_project: Callable[[IncludeSearch], object],
    policy: IncludeSearch,
    provider: str,
) -> None:
    assert load_layered_project(policy) == {
        "common": ["lint", ["unit", "integration"], "lint"],
        "empty": [],
        "stage": "application",
        "flavor": provider,
        "fallback_value": True,
        "database": {"host": f"{provider}.db", "port": 5432},
        "service": {
            "host": f"{provider}.example.test",
            "port": 9443,
            "cleared": None,
            "labels": ["application"],
            "limits": {"requests": 1000},
            "unchanged": {"flag": True},
            "cleared_mapping": {},
            "nested": {"retries": 5, "timeout": 45, "fallback_only": True},
        },
        "deployment_steps": ["production-start", f"{provider}-deployment", "production-end"],
        "steps": [
            "main-start",
            "lint",
            ["unit", "integration"],
            "lint",
            f"{provider}-before",
            f"{provider}-task",
            [f"{provider}-nested"],
            f"{provider}-after",
            "lint",
            ["unit", "integration"],
            "lint",
            "main-end",
        ],
    }


def test_searched_files_resolve_neighbors_and_fall_through_search_directories(
    test_path: Path,
) -> None:
    write_sources(
        test_path,
        {
            "app/main.yaml": "%LOADER YamlCompose\n---\nconfig: !include bundle.yaml\n",
            "first/bundle.yaml": """
            <<<: !include ./base.yaml
            extras: !include extras.yaml
            steps: [before, !spread [!include steps.yaml], after]
        """,
            "first/base.yaml": "selected: first\n",
            "second/bundle.yaml": "selected: wrong-second-bundle\n",
            "second/extras.yaml": "selected: second\n",
            "second/steps.yaml": "- !spread [!include ./parts.yaml]\n",
            "second/parts.yaml": "[second-step]\n",
        },
    )
    assert load_yaml_file(
        test_path / "app/main.yaml",
        search_paths=[test_path / "first", test_path / "second"],
        include_search="search-first",
    ) == {
        "config": {
            "selected": "first",
            "extras": {"selected": "second"},
            "steps": ["before", "second-step", "after"],
        }
    }


def test_spread_mapping_items_are_completed_values_and_do_not_mutate_sources(
    test_path: Path,
) -> None:
    write_sources(
        test_path,
        {
            "main.yaml": """
            %LOADER YamlCompose
            ---
            base: &base {items: [inherited], keep: true}
            included: !include ./lists/items.yaml
            config:
              <<<: *base
              items: [before, !spread [!include ./lists/items.yaml], after]
        """,
            "lists/items.yaml": """
            - &template {host: localhost, token: secret, flags: [base]}
            - <<<: *template
              token: !delete
              flags: [!spread [!include ./flags.yaml], local]
            - <<: *template
              host: shallow
        """,
            "lists/flags.yaml": "[file-a, file-b]\n",
            "search/items.yaml": "[wrong-items]\n",
            "search/flags.yaml": "[wrong-flags]\n",
        },
    )
    template = {"host": "localhost", "token": "secret", "flags": ["base"]}
    edited = {"host": "localhost", "flags": ["file-a", "file-b", "local"]}
    shallow = {"host": "shallow", "token": "secret", "flags": ["base"]}
    assert load_yaml_file(
        test_path / "main.yaml", search_paths=[test_path / "search"], include_search="search-first"
    ) == {
        "base": {"items": ["inherited"], "keep": True},
        "included": [template, edited, shallow],
        "config": {"items": ["before", template, edited, shallow, "after"], "keep": True},
    }


def test_completed_includes_do_not_carry_delete_or_replace_instructions(test_path: Path) -> None:
    write_sources(
        test_path,
        {
            "base.yaml": "service: {host: localhost, token: secret, options: {a: 1, b: 2}}\n",
            "environment.yaml": """
            <<<: !include ./base.yaml
            service:
              token: !delete
              options: !replace {environment: true}
        """,
            "main.yaml": """
            %LOADER YamlCompose
            ---
            direct: !include ./environment.yaml
            recombined:
              <<<: [!include ./environment.yaml, !include ./base.yaml]
        """,
        },
    )
    assert load_yaml_file(test_path / "main.yaml") == {
        "direct": {"service": {"host": "localhost", "options": {"environment": True}}},
        "recombined": {
            "service": {
                "host": "localhost",
                "token": "secret",
                "options": {"a": 1, "b": 2, "environment": True},
            }
        },
    }


def test_type_changes_in_a_file_chain_discard_earlier_mapping_keys(test_path: Path) -> None:
    write_sources(
        test_path,
        {
            "base.yaml": "service: {host: localhost, port: 8080}\nitems: [base]\n",
            "environment.yaml": "<<<: !include ./base.yaml\nservice: null\nitems: []\n",
            "main.yaml": """
                %LOADER YamlCompose
                ---
                <<<: !include ./environment.yaml
                service: {port: 9090}
            """,
        },
    )
    assert load_yaml_file(test_path / "main.yaml") == {"service": {"port": 9090}, "items": []}


@pytest.mark.parametrize(
    ("resource", "message"),
    [
        ("depth", "maximum include depth"),
        ("files", "maximum include count"),
        ("file_bytes", "per-file size limit"),
        ("total_bytes", "maximum total included bytes"),
    ],
)
def test_repeated_nested_spreads_share_budgets_and_accept_exact_boundaries(
    test_path: Path, resource: str, message: str
) -> None:
    write_sources(
        test_path,
        {
            "main.yaml": """
            %LOADER YamlCompose
            ---
            items:
              - !spread [!include ./shared.yaml]
              - !spread [!include ./shared.yaml]
        """,
            "shared.yaml": "- !spread [!include ./leaf.yaml]\n",
            "leaf.yaml": "- café\n",
        },
    )
    shared_bytes = (test_path / "shared.yaml").stat().st_size
    leaf_bytes = (test_path / "leaf.yaml").stat().st_size
    bounds = {
        "depth": 2,
        "files": 4,
        "file_bytes": max(shared_bytes, leaf_bytes),
        "total_bytes": 2 * (shared_bytes + leaf_bytes),
    }

    def loader(reduction: int) -> YamlConfigLoader[YamlType]:
        limits = {**bounds, resource: bounds[resource] - reduction}
        return YamlConfigLoader(
            max_include_depth=limits["depth"],
            max_include_files=limits["files"],
            max_file_bytes=limits["file_bytes"],
            max_total_include_bytes=limits["total_bytes"],
        )

    assert loader(0).load_file(test_path / "main.yaml") == {"items": ["café", "café"]}
    with pytest.raises(yaml.YAMLError, match=message):
        loader(1).load_file(test_path / "main.yaml")


@pytest.mark.parametrize(
    ("contents", "message", "filename", "line"),
    [
        (
            "items:\n  - !spread [!include ./scalar.yaml]\n",
            "source must be a sequence",
            "bad.yaml",
            2,
        ),
        ("items:\n  - !spread [!include ./missing.yaml]\n", "was not found", "bad.yaml", 2),
        ("<<<: !include ./scalar.yaml\n", "source for <<< must be a mapping", "bad.yaml", 1),
        ("items: [one]\nitems: [two]\n", "duplicate key", "bad.yaml", 2),
        ("items:\n  - !spread [!include ./syntax.yaml]\n", "expected", "syntax.yaml", 2),
    ],
)
def test_invalid_nested_sources_fail_even_when_a_local_list_would_replace_them(
    test_path: Path, contents: str, message: str, filename: str, line: int
) -> None:
    write_sources(
        test_path,
        {
            "main.yaml": """
                %LOADER YamlCompose
                ---
                <<<: !include ./nested/config.yaml
                items: [local]
            """,
            "nested/config.yaml": "<<<: !include ./bad.yaml\nitems: [nested]\n",
            "nested/bad.yaml": contents,
            "nested/scalar.yaml": "wrong-type\n",
            "nested/syntax.yaml": "items: [broken\n",
        },
    )
    with pytest.raises(yaml.YAMLError, match=message) as caught:
        load_yaml_file(test_path / "main.yaml")
    assert filename in str(caught.value)
    assert f"line {line}" in str(caught.value)


def test_cycle_through_a_spread_reports_the_nested_reference(test_path: Path) -> None:
    write_sources(
        test_path,
        {
            "main.yaml": """
                %LOADER YamlCompose
                ---
                items: [!spread [!include ./nested/items.yaml]]
            """,
            "nested/items.yaml": "- !spread [!include ../main.yaml]\n",
        },
    )
    with pytest.raises(yaml.YAMLError, match="include cycle detected") as caught:
        load_yaml_file(test_path / "main.yaml")
    assert "items.yaml" in str(caught.value)
    assert "main.yaml" in str(caught.value)
    assert "line 1" in str(caught.value)


def test_nested_spread_cannot_escape_roots_to_read_a_sequence(test_path: Path) -> None:
    write_sources(
        test_path,
        {
            "project/main.yaml": """
                %LOADER YamlCompose
                ---
                items: [!spread [!include ./nested/items.yaml]]
            """,
            "project/nested/items.yaml": "- !spread [!include ../../outside.yaml]\n",
            "outside.yaml": "[outside]\n",
        },
    )
    with pytest.raises(yaml.YAMLError, match="outside the allowed roots") as caught:
        load_yaml_file(test_path / "project/main.yaml")
    assert "items.yaml" in str(caught.value)
    assert "line 1" in str(caught.value)


def test_failed_nested_spread_does_not_poison_a_reused_loader(test_path: Path) -> None:
    write_sources(
        test_path,
        {
            "main.yaml": "%LOADER YamlCompose\n---\nitems: [!spread [!include ./items.yaml]]\n",
            "items.yaml": "- !spread [!include ./leaf.yaml]\n",
            "leaf.yaml": "wrong-type\n",
        },
    )
    loader = YamlConfigLoader(max_include_files=2, max_include_depth=2)
    with pytest.raises(yaml.YAMLError, match="source must be a sequence"):
        loader.load_file(test_path / "main.yaml")
    write_sources(test_path, {"leaf.yaml": "[fixed]\n"})
    assert loader.load_file(test_path / "main.yaml") == {"items": ["fixed"]}
