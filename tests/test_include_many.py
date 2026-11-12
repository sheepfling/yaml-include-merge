"""Object-only source collections preserve per-file semantics and shared limits."""

from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO, StringIO
from pathlib import Path, PurePosixPath

import pytest
import yaml

from yaml_compose import (
    INCLUDE_TAG,
    FileRoot,
    FileSource,
    IncludeLimits,
    IncludeRequest,
    IncludeSource,
    JsonPathLimits,
    MemoryIncludeResolver,
    MemoryRoot,
    MemorySource,
    ResolvedInclude,
    YamlConfigLoader,
    get_include_route,
    load_yaml_compose,
)
from yaml_compose import loader as loader_module
from yaml_compose._glob import expand_include_glob


def load_many(options: str, files: Mapping[str, str | bytes], *, limits: IncludeLimits | None = None) -> object:
    root = MemoryRoot("many")
    return load_yaml_compose(
        StringIO("!include " + options),
        allowed_roots=[root],
        include_resolver=MemoryIncludeResolver(root, files),
        include_limits=limits or IncludeLimits(),
    )


def test_empty_and_ordered_sources_preserve_duplicates_and_nested_values() -> None:
    assert INCLUDE_TAG == "!include"
    with pytest.raises(yaml.YAMLError):
        load_many("{sources: []}", {}, limits=IncludeLimits(max_include_files=0))
    files = {"a.yaml": "[one, two]", "b.json": '{"x": 3}'}
    assert load_many("{sources: [b.json, {path: a.yaml}, b.json]}", files) == [{"x": 3}, ["one", "two"], {"x": 3}]


def test_pointer_applies_to_each_completed_source_not_aggregate() -> None:
    files = {"a.yaml": "x: [one, two]", "b.yaml": "x: [three]"}
    assert load_many("{sources: [a.yaml, b.yaml], json_pointer: /x}", files) == [["one", "two"], ["three"]]
    assert load_many("{sources: [a.yaml], json_pointer: ''}", files) == [{"x": ["one", "two"]}]
    with pytest.raises(yaml.YAMLError, match="mapping key '0'"):
        load_many("{sources: [a.yaml], json_pointer: /0}", files)


@pytest.mark.parametrize("query_results", [[], [1], [[1, 2], 3]])
def test_jsonpath_preserves_each_source_nodelist_and_policy(
    monkeypatch: pytest.MonkeyPatch, query_results: list[object]
) -> None:
    calls: list[tuple[object, str, JsonPathLimits]] = []
    limits = JsonPathLimits(max_matches=3)

    def select(value: object, query: str, policy: JsonPathLimits) -> list[object]:
        calls.append((value, query, policy))
        return query_results

    monkeypatch.setattr("yaml_compose.loader.select_jsonpath", select)
    root = MemoryRoot("jsonpath-many")
    result = load_yaml_compose(
        StringIO("!include {sources: [a.yaml, b.yaml], json_path: '$.x'}"),
        include_resolver=MemoryIncludeResolver(root, {"a.yaml": "x: 1", "b.yaml": "x: 2"}),
        allowed_roots=[root],
        jsonpath_limits=limits,
    )
    assert result == [query_results, query_results]
    assert calls == [({"x": 1}, "$.x", limits), ({"x": 2}, "$.x", limits)]


def test_documents_all_retains_per_file_document_lists_and_selects_from_them() -> None:
    files = {"a.yaml": "---\nx: 1\n---\nx: [2, 3]", "b.yaml": ""}
    assert load_many("{sources: [a.yaml, b.yaml], documents: all}", files) == [[{"x": 1}, {"x": [2, 3]}], []]
    assert load_many("{sources: [a.yaml], documents: all, json_pointer: /1/x}", files) == [[2, 3]]
    with pytest.raises(yaml.YAMLError, match="expected a single document"):
        load_many("{sources: [a.yaml]}", files)
    with pytest.raises(yaml.YAMLError, match="requires a YAML source"):
        load_many("{sources: [a.json], documents: all}", {"a.json": "{}"})


def test_late_document_errors_and_nested_cycles_are_not_hidden() -> None:
    with pytest.raises(yaml.YAMLError, match="duplicate"):
        load_many("{sources: [a.yaml], documents: all, json_pointer: /0}", {"a.yaml": "x: 1\n---\nx: 1\nx: 2"})
    files = {"a.yaml": "x: 1\nother: !include {sources: [a.yaml]}"}
    with pytest.raises(yaml.YAMLError, match="include cycle") as error:
        load_many("{sources: [a.yaml], json_pointer: /x}", files)
    assert [frame.tag for frame in get_include_route(error.value)] == [INCLUDE_TAG, INCLUDE_TAG]


@pytest.mark.parametrize(
    "options",
    [
        "[]",
        "{}",
        "{sources: []}",
        "{path: x, sources: [a.yaml]}",
        "{sources: null}",
        "{sources: file.yaml}",
        "{sources: [null]}",
        "{sources: [true]}",
        "{sources: [42]}",
        "{sources: ['']}",
        "{sources: [[]]}",
        "{sources: [{}]}",
        "{sources: [{glob: ''}]}",
        "{sources: [{path: a, glob: b}]}",
        "{sources: [{glob: a, glob: b}]}",
        "{sources: [!include name.yaml]}",
        "{sources: [{path: !include name.yaml}]}",
        "{sources: !custom [a.yaml]}",
        "{sources: [!custom {path: a.yaml}]}",
        "{sources: [a.yaml], json_pointer: /a, json_path: '$'}",
        "{sources: [a.yaml], json_pointer: '#/a'}",
        "{sources: [a.yaml], json_path: null}",
        "{sources: [a.yaml], documents: true}",
        "{sources: [a.yaml], documents: one}",
        "{sources: [a.yaml], sources: []}",
        "{sources: [], allow_absolute_includes: true}",
        "{sources: [a.yaml, 'https://example.invalid/x']}",
        "{sources: [a.yaml, {glob: '*.yaml'}]}",
    ],
)
def test_invalid_shape_names_and_glob_resolver_fail_before_any_resolution(options: str) -> None:
    root = MemoryRoot("never")

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            raise AssertionError(f"invalid spec reached resolver: {request.name}")

    with pytest.raises(yaml.YAMLError) as error:
        load_yaml_compose(StringIO("!include " + options), allowed_roots=[root], include_resolver=Resolver())
    assert get_include_route(error.value)[-1].tag == INCLUDE_TAG


@pytest.mark.parametrize(
    "limits",
    [
        IncludeLimits(max_include_files=1),
        IncludeLimits(max_include_depth=0),
        IncludeLimits(max_file_bytes=1),
        IncludeLimits(max_total_include_bytes=5),
    ],
)
def test_every_source_uses_shared_include_limits(limits: IncludeLimits) -> None:
    with pytest.raises(yaml.YAMLError, match=r"limit|maximum"):
        load_many("{sources: [a.yaml, a.yaml], json_pointer: /x}", {"a.yaml": "x: 1"}, limits=limits)


def test_nested_counts_and_depth_are_file_reads_not_collection_wrappers() -> None:
    files = {"a.yaml": "x: !include {sources: [b.yaml, c.yaml]}", "b.yaml": "2", "c.yaml": "3"}
    assert load_many("{sources: [a.yaml]}", files, limits=IncludeLimits(max_include_files=3, max_include_depth=2)) == [
        {"x": [2, 3]}
    ]
    with pytest.raises(yaml.YAMLError, match="maximum include count"):
        load_many("{sources: [a.yaml]}", files, limits=IncludeLimits(max_include_files=2))
    with pytest.raises(yaml.YAMLError, match="maximum include depth"):
        load_many("{sources: [a.yaml]}", files, limits=IncludeLimits(max_include_depth=1))


def test_custom_loader_registration_and_source_marks() -> None:
    class Loader(yaml.SafeLoader):
        pass

    root = MemoryRoot("configured")
    resolver = MemoryIncludeResolver(root, {"a.yaml": "1"})
    assert load_yaml_compose(
        StringIO("!include {sources: [a.yaml]}"), allowed_roots=[root], include_resolver=resolver, yaml_loader=Loader
    ) == [1]
    with pytest.raises(yaml.YAMLError) as error:
        load_yaml_compose(
            StringIO("value: !include\n  sources:\n    - a.yaml\n    - missing.yaml\n"),
            allowed_roots=[root],
            include_resolver=resolver,
        )
    frame = get_include_route(error.value)[-1]
    assert (frame.tag, frame.requested_path, frame.line) == (INCLUDE_TAG, "missing.yaml", 4)


def test_custom_resolver_cannot_return_outside_source() -> None:
    allowed, outside = MemoryRoot("allowed"), MemoryRoot("outside")
    opened = False

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            def open_binary() -> BytesIO:
                nonlocal opened
                opened = True
                return BytesIO(b"1")

            return ResolvedInclude(MemorySource(outside, PurePosixPath("x")), open_binary)

    with pytest.raises(yaml.YAMLError, match="outside the allowed roots"):
        load_yaml_compose(StringIO("!include {sources: [x]}"), allowed_roots=[allowed], include_resolver=Resolver())
    assert not opened


def test_glob_steps_and_matches_are_bounded_before_reads(test_path: Path) -> None:
    (test_path / "a.yaml").write_text("1", encoding="utf-8")
    (test_path / "b.yaml").write_text("2", encoding="utf-8")
    for limits, message in (
        (IncludeLimits(max_glob_steps=0), "glob traversal"),
        (IncludeLimits(max_include_files=1), "count"),
    ):
        with pytest.raises(yaml.YAMLError, match=message):
            load_yaml_compose(
                StringIO("!include {sources: [{glob: '*.yaml'}]}"),
                search_paths=[test_path],
                allowed_roots=[test_path],
                include_limits=limits,
            )
    assert load_yaml_compose(
        StringIO("!include {sources: [a.yaml]}"),
        search_paths=[test_path],
        allowed_roots=[test_path],
        include_limits=IncludeLimits(max_glob_steps=0),
    ) == [1]


@pytest.mark.parametrize("invalid", [True, -1, 1.5, "10"])
def test_invalid_glob_step_profiles_are_rejected(invalid: object) -> None:
    with pytest.raises(ValueError, match="include limits"):
        IncludeLimits(max_glob_steps=invalid)  # pyright: ignore[reportArgumentType]


def test_glob_profile_is_snapshotted_and_mutable_like_existing_limits() -> None:
    profile = IncludeLimits(max_glob_steps=10)
    loader = YamlConfigLoader(include_limits=profile)
    profile.max_glob_steps = 20
    assert loader.include_limits.max_glob_steps == 10


def test_glob_steps_are_shared_across_repeated_and_nested_expansions(test_path: Path) -> None:
    (test_path / "a.json").write_text("1", encoding="utf-8")
    (test_path / "outer.yaml").write_text("!include {sources: [{glob: '*.json'}]}", encoding="utf-8")
    steps = 0

    def count_step() -> None:
        nonlocal steps
        steps += 1

    root = FileRoot(test_path)
    expand_include_glob(IncludeRequest("*.json", None, (root,), (root,)), step=count_step, max_matches=None)
    assert steps > 0
    loader = YamlConfigLoader(
        search_paths=[test_path], allowed_roots=[test_path], include_limits=IncludeLimits(max_glob_steps=steps)
    )
    for _ in range(2):
        assert loader.load_include(StringIO("!include {sources: [{glob: '*.json'}]}")) == [1]
    with pytest.raises(yaml.YAMLError, match="maximum glob traversal"):
        loader.load_include(StringIO("!include {sources: [{glob: '*.json'}, {glob: '*.json'}]}"))
    with pytest.raises(yaml.YAMLError, match="maximum glob traversal"):
        loader.load_include(StringIO("!include {sources: [{glob: 'outer.yaml'}]}"))
    assert load_yaml_compose(
        StringIO("!include {sources: [{glob: 'outer.yaml'}]}"),
        search_paths=[test_path],
        allowed_roots=[test_path],
        include_limits=IncludeLimits(max_glob_steps=2 * steps),
    ) == [[1]]
    assert load_yaml_compose(
        StringIO("!include {sources: [{glob: '*.json'}, {glob: '*.json'}]}"),
        search_paths=[test_path],
        allowed_roots=[test_path],
        include_limits=IncludeLimits(max_glob_steps=None),
    ) == [1, 1]


def test_glob_traversal_budget_counts_unmatched_entries(test_path: Path) -> None:
    (test_path / "a.json").write_text("1", encoding="utf-8")
    for index in range(10):
        (test_path / f"unmatched-{index}.txt").write_text("x", encoding="utf-8")
    with pytest.raises(yaml.YAMLError, match="glob traversal"):
        load_yaml_compose(
            StringIO("!include {sources: [{glob: '*.json'}]}"),
            search_paths=[test_path],
            allowed_roots=[test_path],
            include_limits=IncludeLimits(max_glob_steps=5),
        )


def test_invalid_entry_error_names_the_path_without_opening_valid_predecessors() -> None:
    with pytest.raises(yaml.YAMLError) as error:
        load_many("{sources: [missing.yaml, 'https://example.invalid/bad']}", {})
    assert get_include_route(error.value)[-1].requested_path == "https://example.invalid/bad"


def test_many_results_spread_explicitly_and_do_not_implicitly_merge() -> None:
    root = MemoryRoot("spread-many")
    resolver = MemoryIncludeResolver(root, {"a.yaml": "[one, two]", "b.yaml": "value: 2"})
    assert load_yaml_compose(
        StringIO("[before, !spread [!include {sources: [a.yaml, b.yaml]}], after]"),
        allowed_roots=[root],
        include_resolver=resolver,
    ) == ["before", ["one", "two"], {"value": 2}, "after"]
    with pytest.raises(yaml.YAMLError, match="must be a mapping") as error:
        load_yaml_compose(
            StringIO("<<<: !include {sources: [b.yaml]}"),
            allowed_roots=[root],
            include_resolver=resolver,
        )
    assert get_include_route(error.value)[-1].tag == INCLUDE_TAG


def test_glob_opener_keeps_checked_canonical_target_if_alias_changes(
    test_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inside = test_path / "allowed"
    inside.mkdir()
    target = inside / "target.yaml"
    target.write_text("safe", encoding="utf-8")
    outside = test_path / "outside.yaml"
    outside.write_text("unsafe", encoding="utf-8")
    alias = inside / "alias.yaml"
    alias.symlink_to(target)
    original = loader_module._canonical_source  # pyright: ignore[reportPrivateUsage]
    switched = False

    def canonical(source: IncludeSource) -> IncludeSource:
        nonlocal switched
        result = original(source)
        if not switched and result == FileSource(target):
            alias.unlink()
            alias.symlink_to(outside)
            switched = True
        return result

    monkeypatch.setattr(loader_module, "_canonical_source", canonical)
    assert load_yaml_compose(
        StringIO("!include {sources: [{glob: alias.yaml}]}"),
        search_paths=[inside],
        allowed_roots=[inside],
    ) == ["safe"]
    assert switched


def test_documents_all_jsonpath_runs_once_per_document_list(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[object] = []

    def select(value: object, query: str, policy: JsonPathLimits) -> list[object]:
        captured.append(value)
        return [2]

    monkeypatch.setattr("yaml_compose.loader.select_jsonpath", select)
    assert load_many("{sources: [a.yaml], documents: all, json_path: '$[1].x'}", {"a.yaml": "x: 1\n---\nx: 2"}) == [[2]]
    assert captured == [[{"x": 1}, {"x": 2}]]
