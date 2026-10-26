from __future__ import annotations

import os
from io import StringIO
from pathlib import Path, PurePosixPath
from typing import cast

import pytest
import yaml

from yaml_compose import (
    FileRoot,
    FileSystemIncludeResolver,
    IncludeRequest,
    IncludeSearch,
    MemoryIncludeResolver,
    MemoryRoot,
    MemorySource,
    ResolvedInclude,
    YamlConfigLoader,
    load_yaml_compose,
    load_yaml_dispatch,
    load_yaml_file,
)


def write(root: Path, name: str, body: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ("local-first", "local"),
        ("search-first", "search"),
        ("local-only", "local"),
        ("search-only", "search"),
    ],
)
def test_bare_names_follow_the_selected_search_policy(
    test_path: Path, policy: IncludeSearch, expected: str
) -> None:
    write(test_path, "app/value.yaml", "local\n")
    write(test_path, "shared/value.yaml", "search\n")
    config = write(test_path, "app/main.yaml", "%LOADER YamlCompose\n---\n!include value.yaml\n")
    assert (
        load_yaml_file(config, search_paths=[test_path / "shared"], include_search=policy)
        == expected
    )
    assert load_yaml_file(config, search_paths=[test_path / "shared"]) == "local"


@pytest.mark.parametrize("policy", ["local-first", "search-first", "local-only", "search-only"])
@pytest.mark.parametrize("reference", ["./value.yaml", "../value.yaml"])
def test_explicit_relative_paths_bypass_search_precedence(
    test_path: Path, policy: IncludeSearch, reference: str
) -> None:
    write(test_path, "app/value.yaml", "sibling\n")
    write(test_path, "value.yaml", "parent\n")
    write(test_path, "shared/value.yaml", "search\n")
    config = write(test_path, "app/main.yaml", f"%LOADER YamlCompose\n---\n!include {reference}\n")
    assert load_yaml_file(
        config,
        search_paths=[test_path / "shared"],
        allowed_roots=[test_path],
        include_search=policy,
    ) == ("sibling" if reference.startswith("./") else "parent")


@pytest.mark.parametrize("policy", ["local-first", "search-first", "local-only", "search-only"])
def test_missing_explicit_relative_paths_never_fall_back(
    test_path: Path, policy: IncludeSearch
) -> None:
    write(test_path, "shared/value.yaml", "search\n")
    config = write(test_path, "app/main.yaml", "%LOADER YamlCompose\n---\n!include ./value.yaml\n")
    with pytest.raises(yaml.YAMLError, match="was not found"):
        load_yaml_file(config, search_paths=[test_path / "shared"], include_search=policy)


@pytest.mark.parametrize(("policy", "location"), [("local-only", "shared"), ("search-only", "app")])
def test_restricted_modes_do_not_consult_the_other_location(
    test_path: Path, policy: IncludeSearch, location: str
) -> None:
    (test_path / "shared").mkdir()
    write(test_path, f"{location}/value.yaml", "found\n")
    config = write(test_path, "app/main.yaml", "%LOADER YamlCompose\n---\n!include value.yaml\n")
    with pytest.raises(yaml.YAMLError, match="was not found"):
        load_yaml_file(config, search_paths=[test_path / "shared"], include_search=policy)


@pytest.mark.parametrize("policy", ["local-first", "search-first", "search-only"])
def test_search_directories_keep_the_callers_order(test_path: Path, policy: IncludeSearch) -> None:
    write(test_path, "first/value.yaml", "first\n")
    write(test_path, "second/value.yaml", "second\n")
    config = write(test_path, "app/main.yaml", "%LOADER YamlCompose\n---\n!include value.yaml\n")
    assert (
        load_yaml_file(
            config, search_paths=[test_path / "first", test_path / "second"], include_search=policy
        )
        == "first"
    )


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ("local-first", "near-child"),
        ("search-first", "shared"),
        ("local-only", "near-child"),
        ("search-only", "shared"),
    ],
)
def test_nested_includes_use_their_own_directory_and_inherit_the_policy(
    test_path: Path, policy: IncludeSearch, expected: str
) -> None:
    write(test_path, "app/value.yaml", "near-main\n")
    write(test_path, "app/nested/value.yaml", "near-child\n")
    write(test_path, "app/nested/child.yaml", "value: !include value.yaml\n")
    write(test_path, "shared/value.yaml", "shared\n")
    config = write(
        test_path, "app/main.yaml", "%LOADER YamlCompose\n---\n!include ./nested/child.yaml\n"
    )
    assert load_yaml_file(config, search_paths=[test_path / "shared"], include_search=policy) == {
        "value": expected
    }


def test_local_fallback_works_after_all_search_directories_miss(test_path: Path) -> None:
    (test_path / "shared").mkdir()
    write(test_path, "app/value.yaml", "local\n")
    config = write(test_path, "app/main.yaml", "%LOADER YamlCompose\n---\n!include value.yaml\n")
    assert (
        load_yaml_file(config, search_paths=[test_path / "shared"], include_search="search-first")
        == "local"
    )


def test_explicit_relative_paths_require_a_source_even_with_search_directories(
    test_path: Path,
) -> None:
    write(test_path, "value.yaml", "found\n")
    with pytest.raises(yaml.YAMLError, match="require an including source"):
        load_yaml_compose(StringIO("!include ./value.yaml\n"), search_paths=[test_path])
    assert (
        load_yaml_compose(
            StringIO("!include value.yaml\n"),
            search_paths=[test_path],
            include_search="search-only",
        )
        == "found"
    )


def test_absolute_names_resolve_exactly_under_any_policy(test_path: Path) -> None:
    value = write(test_path, "value.yaml", "exact\n")
    config = write(
        test_path, "main.yaml", f"%LOADER YamlCompose\n---\n!include {value.as_posix()}\n"
    )
    assert load_yaml_file(config, include_search="search-only") == "exact"
    assert load_yaml_file(config, allow_absolute_includes=True) == "exact"


@pytest.mark.parametrize(
    "entry", ["file", "dispatch", "compose", "facade-file", "facade-stream", "facade-include"]
)
def test_absolute_include_policy_is_forwarded_by_every_entry_point(
    test_path: Path, entry: str
) -> None:
    value = write(test_path, "value.yaml", "exact\n")
    include = f"!include {value.as_posix()}\n"
    document = f"%LOADER YamlCompose\n---\n{include}"
    config = write(test_path, "main.yaml", document)
    loader = YamlConfigLoader(allow_absolute_includes=False)

    with pytest.raises(yaml.YAMLError, match="include paths must be relative paths"):
        if entry == "file":
            load_yaml_file(config, allow_absolute_includes=False)
        elif entry == "dispatch":
            load_yaml_dispatch(
                StringIO(document), source_path=config, allow_absolute_includes=False
            )
        elif entry == "compose":
            load_yaml_compose(StringIO(include), source_path=config, allow_absolute_includes=False)
        elif entry == "facade-file":
            loader.load_file(config)
        elif entry == "facade-stream":
            loader.load_stream(StringIO(document), source_path=config)
        else:
            loader.load_include(StringIO(include), source_path=config)


def test_absolute_include_policy_is_inherited_by_nested_documents(test_path: Path) -> None:
    value = write(test_path, "value.yaml", "exact\n")
    write(test_path, "child.yaml", f"value: !include {value.as_posix()}\n")
    config = write(test_path, "main.yaml", "%LOADER YamlCompose\n---\n!include child.yaml\n")

    with pytest.raises(yaml.YAMLError, match="include paths must be relative paths"):
        load_yaml_file(config, allow_absolute_includes=False)


def test_absolute_include_is_rejected_before_custom_resolver(test_path: Path) -> None:
    class UnexpectedResolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            raise AssertionError(f"resolver called for {request.name!r}")

    value = write(test_path, "value.yaml", "exact\n")
    config = write(
        test_path, "main.yaml", f"%LOADER YamlCompose\n---\n!include {value.as_posix()}\n"
    )
    with pytest.raises(yaml.YAMLError, match="include paths must be relative paths"):
        load_yaml_file(config, include_resolver=UnexpectedResolver(), allow_absolute_includes=False)


def test_disallowing_absolute_paths_keeps_parent_paths_within_allowed_roots(
    test_path: Path,
) -> None:
    write(test_path, "shared.yaml", "inside\n")
    config = write(
        test_path, "app/main.yaml", "%LOADER YamlCompose\n---\n!include ../shared.yaml\n"
    )

    assert (
        load_yaml_file(config, allowed_roots=[test_path], allow_absolute_includes=False) == "inside"
    )
    with pytest.raises(yaml.YAMLError, match="outside the allowed roots"):
        load_yaml_file(config, allow_absolute_includes=False)


@pytest.mark.parametrize("invalid", [None, "false", 0])
def test_absolute_include_policy_requires_a_bool(invalid: object) -> None:
    policy = cast(bool, invalid)
    with pytest.raises(ValueError, match="allow_absolute_includes must be a bool"):
        YamlConfigLoader(allow_absolute_includes=policy)
    with pytest.raises(ValueError, match="allow_absolute_includes must be a bool"):
        load_yaml_dispatch(StringIO("value: ordinary\n"), allow_absolute_includes=policy)


def test_platform_relative_prefix_stays_pinned_under_search_first(test_path: Path) -> None:
    write(test_path, "app/value.yaml", "local\n")
    write(test_path, "shared/value.yaml", "shared\n")
    reference = f".{os.sep}value.yaml"
    config = write(test_path, "app/main.yaml", f"%LOADER YamlCompose\n---\n!include {reference}\n")
    assert (
        load_yaml_file(config, search_paths=[test_path / "shared"], include_search="search-first")
        == "local"
    )


@pytest.mark.parametrize("policy", ["local-first", "search-first"])
def test_search_selection_does_not_change_merge_priority_or_list_spread_order(
    test_path: Path, policy: IncludeSearch
) -> None:
    for directory in ("app", "shared"):
        write(
            test_path,
            f"{directory}/first.yaml",
            f"flavor: {directory}-first\nservice: {{host: {directory}}}\n",
        )
        write(
            test_path,
            f"{directory}/later.yaml",
            f"flavor: {directory}-later\nservice: {{port: 8080}}\n",
        )
        write(test_path, f"{directory}/items.yaml", f"[{directory}]\n")
    config = write(
        test_path,
        "app/main.yaml",
        "%LOADER YamlCompose\n---\n<<<: [!include first.yaml, !include later.yaml]\n"
        "service: {port: 9090}\nitems: [before, !spread [!include items.yaml], after]\n",
    )
    selected = "app" if policy == "local-first" else "shared"
    assert load_yaml_file(config, search_paths=[test_path / "shared"], include_search=policy) == {
        "flavor": f"{selected}-first",
        "service": {"host": selected, "port": 9090},
        "items": ["before", selected, "after"],
    }


def test_all_loading_entry_points_forward_the_search_policy(test_path: Path) -> None:
    write(test_path, "app/value.yaml", "local\n")
    write(test_path, "shared/value.yaml", "search\n")
    text = "%LOADER YamlCompose\n---\n!include value.yaml\n"
    config = write(test_path, "app/main.yaml", text)
    loader = YamlConfigLoader(search_paths=[test_path / "shared"], include_search="search-first")
    assert loader.load_file(config) == "search"
    assert loader.load_stream(StringIO(text), source_path=config) == "search"
    assert loader.load_include(StringIO("!include value.yaml\n"), source_path=config) == "search"
    assert (
        load_yaml_dispatch(
            StringIO(text),
            source_path=config,
            search_paths=[test_path / "shared"],
            include_search="search-first",
        )
        == "search"
    )


@pytest.mark.parametrize("invalid", [None, True, "unknown", []])
def test_unknown_policies_are_rejected_before_loading(test_path: Path, invalid: object) -> None:
    policy = cast(IncludeSearch, invalid)
    with pytest.raises(ValueError, match="include_search must be"):
        YamlConfigLoader(include_search=policy)
    with pytest.raises(ValueError, match="include_search must be"):
        load_yaml_dispatch(StringIO("value: ordinary\n"), include_search=policy)
    with pytest.raises(ValueError, match="include_search must be"):
        IncludeRequest("value.yaml", None, (), (), include_search=policy)


def test_search_priority_does_not_bypass_allowed_roots(test_path: Path) -> None:
    write(test_path, "inside/value.yaml", "inside\n")
    write(test_path, "outside/value.yaml", "outside\n")
    config = write(test_path, "inside/main.yaml", "%LOADER YamlCompose\n---\n!include value.yaml\n")
    with pytest.raises(yaml.YAMLError, match="outside the allowed roots"):
        load_yaml_file(
            config,
            allowed_roots=[test_path / "inside"],
            search_paths=[test_path / "outside"],
            include_search="search-first",
        )
    assert (
        load_yaml_file(
            config,
            allowed_roots=[test_path / "inside"],
            search_paths=[test_path / "outside"],
            include_search="local-first",
        )
        == "inside"
    )


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ("local-first", "local"),
        ("search-first", "root"),
        ("local-only", "local"),
        ("search-only", "root"),
    ],
)
def test_memory_resolver_applies_policies_between_local_and_namespace_root(
    policy: IncludeSearch, expected: str
) -> None:
    root = MemoryRoot("search")
    resolver = MemoryIncludeResolver(root, {"nested/value.yaml": "local\n", "value.yaml": "root\n"})
    loader = YamlConfigLoader(
        include_resolver=resolver, allowed_roots=[root], include_search=policy
    )
    source = MemorySource(root, PurePosixPath("nested/main.yaml"))
    assert loader.load_include(StringIO("!include value.yaml\n"), source=source) == expected
    assert loader.load_include(StringIO("!include ./value.yaml\n"), source=source) == "local"
    assert loader.load_include(StringIO("!include ../value.yaml\n"), source=source) == "root"


def test_memory_explicit_paths_never_fall_back_to_namespace_root() -> None:
    root = MemoryRoot("search")
    loader = YamlConfigLoader(
        include_resolver=MemoryIncludeResolver(root, {"value.yaml": "root\n"}), allowed_roots=[root]
    )
    with pytest.raises(yaml.YAMLError, match="was not found"):
        loader.load_include(
            StringIO("!include ./value.yaml\n"),
            source=MemorySource(root, PurePosixPath("nested/main.yaml")),
        )


def test_custom_resolver_receives_search_policy_and_normalized_roots(test_path: Path) -> None:
    class RecordingResolver:
        def __init__(self) -> None:
            self.requests: list[IncludeRequest] = []

        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            self.requests.append(request)
            return FileSystemIncludeResolver().resolve(request)

    resolver = RecordingResolver()
    write(test_path, "app/value.yaml", "local\n")
    write(test_path, "shared/value.yaml", "search\n")
    config = write(test_path, "app/main.yaml", "%LOADER YamlCompose\n---\n!include value.yaml\n")
    assert (
        load_yaml_file(
            config,
            include_resolver=resolver,
            include_search="search-first",
            search_paths=[test_path / "shared"],
        )
        == "search"
    )
    assert resolver.requests[0].include_search == "search-first"
    assert resolver.requests[0].search_paths == (FileRoot(test_path / "shared"),)


@pytest.mark.parametrize("tag", ["!overlay", "!patch"])
def test_discarded_ordered_patch_tags_are_rejected(test_path: Path, tag: str) -> None:
    config = write(test_path, "main.yaml", f"%LOADER YamlCompose\n---\n{tag} {{}}\n")
    with pytest.raises(yaml.YAMLError, match="could not determine a constructor"):
        load_yaml_file(config)
