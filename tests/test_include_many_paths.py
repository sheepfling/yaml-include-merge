"""Independent path, ordering, confinement, and result-shape checks for include-many."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from io import BytesIO, StringIO
from pathlib import Path, PurePosixPath

import pytest
import yaml

from yaml_compose import (
    FileRoot,
    IncludeLimits,
    IncludeRequest,
    IncludeSearch,
    MemoryIncludeResolver,
    MemoryRoot,
    MemorySource,
    ResolvedInclude,
    get_include_route,
    load_yaml_compose,
    load_yaml_file,
)
from yaml_compose._glob import expand_include_glob


def _many(sources: list[object], **options: object) -> str:
    return "!include " + json.dumps({"sources": sources, **options}, ensure_ascii=False)


def _main(directory: Path, text: str) -> Path:
    path = directory / "main.yaml"
    path.write_text("%LOADER YamlCompose\n---\n" + text, encoding="utf-8")
    return path


def _symlink(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        pytest.skip(f"native symlink creation unavailable: {error}")


@pytest.mark.parametrize("object_entry", [False, True], ids=["literal-string", "literal-object"])
@pytest.mark.parametrize(
    "name", ["hash#part.json", "semi;colon.json", "percent%20name.json", "with spaces.json", "quote's.json"]
)
def test_native_literal_many_paths(tmp_path: Path, name: str, object_entry: bool) -> None:
    (tmp_path / name).write_text('{"selected": true}', encoding="utf-8")
    entry: object = {"path": name} if object_entry else name
    assert load_yaml_file(_main(tmp_path, _many([entry], json_pointer="/selected"))) == [True]


@pytest.mark.skipif(os.name == "nt", reason="POSIX literal wildcard/colon/backslash filenames, not Windows files")
@pytest.mark.parametrize(
    "name",
    ["star*.json", "question?.json", "bracket[ab].json", "colon:name.json", r"back\slash.json", 'double"quote.json'],
)
@pytest.mark.parametrize("object_entry", [False, True], ids=["literal-string", "literal-object"])
def test_native_posix_literal_metacharacters_are_not_expanded(tmp_path: Path, name: str, object_entry: bool) -> None:
    (tmp_path / name).write_text('"literal"', encoding="utf-8")
    (tmp_path / "starOTHER.json").write_text('"wrong"', encoding="utf-8")
    path = "./" + name
    entry: object = {"path": path} if object_entry else path
    assert load_yaml_file(_main(tmp_path, _many([entry]))) == ["literal"]


def test_literal_glob_characters_with_custom_resolver_are_not_filesystem_patterns() -> None:
    root = MemoryRoot("literal-many")
    name = "literal[*]?#;%20.json"
    resolver = MemoryIncludeResolver(root, {name: '{"value": 1}'})
    assert load_yaml_compose(
        StringIO(_many([name, {"path": name}])), allowed_roots=[root], include_resolver=resolver
    ) == [{"value": 1}, {"value": 1}]


def test_glob_matches_sort_and_duplicates_remain_across_entries(tmp_path: Path) -> None:
    for name, value in [("data-b.json", "B"), ("data-a.json", "A")]:
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
    entries: list[object] = [{"glob": "data-*.json"}, "data-b.json", {"glob": "data-*.json"}]
    main = _main(tmp_path, _many(entries))
    assert load_yaml_file(main) == ["A", "B", "B", "A", "B"]
    with pytest.raises(yaml.YAMLError, match=r"maximum include count|maximum include files"):
        load_yaml_file(main, max_include_files=4)


def test_glob_keeps_hash_percent_semicolon_and_spaces_literal(tmp_path: Path) -> None:
    for name, value in [("group#;%20 a.json", 1), ("group#;%20 b.json", 2), ("group#; a.json", 99)]:
        (tmp_path / name).write_text(str(value), encoding="utf-8")
    assert load_yaml_file(_main(tmp_path, _many([{"glob": "group#;%20 *.json"}]))) == [1, 2]


def test_many_json_pointer_selects_each_source_without_flattening(tmp_path: Path) -> None:
    (tmp_path / "first.json").write_text('{"values": [1, 2]}', encoding="utf-8")
    (tmp_path / "second.json").write_text('{"values": [3]}', encoding="utf-8")
    assert load_yaml_file(_main(tmp_path, _many(["first.json", "second.json"], json_pointer="/values"))) == [
        [1, 2],
        [3],
    ]


def test_many_all_documents_selects_each_completed_stream(tmp_path: Path) -> None:
    (tmp_path / "one.yaml").write_text("---\na: 1\n---\na: 2\n", encoding="utf-8")
    (tmp_path / "two.yaml").write_text("---\na: 3\n---\na: 4\n", encoding="utf-8")
    main = _main(tmp_path, _many(["one.yaml", "two.yaml"], documents="all", json_pointer="/1/a"))
    assert load_yaml_file(main) == [2, 4]
    assert load_yaml_file(_main(tmp_path, _many(["one.yaml"], documents="all"))) == [[{"a": 1}, {"a": 2}]]


def test_many_jsonpath_preserves_each_result_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[object] = []

    def select(value: object, query: str, limits: object) -> list[object]:
        assert query == "$"
        seen.append(value)
        return [value]

    monkeypatch.setattr("yaml_compose.loader.select_jsonpath", select)
    (tmp_path / "one.json").write_text("[1, 2]", encoding="utf-8")
    (tmp_path / "two.json").write_text("3", encoding="utf-8")
    assert load_yaml_file(_main(tmp_path, _many(["one.json", "two.json"], json_path="$"))) == [[[1, 2]], [3]]
    assert seen == [[1, 2], 3]


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ("local-first", ["local"]),
        ("search-first", ["search-a", "search-b"]),
        ("local-only", ["local"]),
        ("search-only", ["search-a", "search-b"]),
    ],
)
def test_bare_glob_uses_only_first_matching_location(tmp_path: Path, policy: IncludeSearch, expected: object) -> None:
    local = tmp_path / "local"
    search = tmp_path / "search"
    local.mkdir()
    search.mkdir()
    (local / "data-z.json").write_text('"local"', encoding="utf-8")
    (search / "data-b.json").write_text('"search-b"', encoding="utf-8")
    (search / "data-a.json").write_text('"search-a"', encoding="utf-8")
    assert (
        load_yaml_file(
            _main(local, _many([{"glob": "data-*.json"}])),
            search_paths=[search],
            include_search=policy,
        )
        == expected
    )


def test_bare_glob_falls_through_empty_location_without_combining_search_paths(tmp_path: Path) -> None:
    local, first, second = (tmp_path / name for name in ("local", "first", "second"))
    for directory in (local, first, second):
        directory.mkdir()
    (first / "data-a.json").write_text("1", encoding="utf-8")
    (second / "data-b.json").write_text("2", encoding="utf-8")
    assert load_yaml_file(_main(local, _many([{"glob": "data-*.json"}])), search_paths=[first, second]) == [1]


def test_explicit_relative_glob_ignores_search_first(tmp_path: Path) -> None:
    local, search = (tmp_path / name for name in ("local", "search"))
    local.mkdir()
    search.mkdir()
    (local / "data-a.json").write_text("1", encoding="utf-8")
    (search / "data-b.json").write_text("2", encoding="utf-8")
    assert load_yaml_file(
        _main(local, _many([{"glob": "./data-*.json"}])), search_paths=[search], include_search="search-first"
    ) == [1]


def test_explicit_relative_glob_requires_containing_source(tmp_path: Path) -> None:
    (tmp_path / "data-a.json").write_text("1", encoding="utf-8")
    with pytest.raises(yaml.YAMLError, match=r"source|containing"):
        load_yaml_compose(
            StringIO(_many([{"glob": "./data-*.json"}])), allowed_roots=[tmp_path], search_paths=[tmp_path]
        )


def test_no_glob_match_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(yaml.YAMLError, match="match"):
        load_yaml_file(_main(tmp_path, _many([{"glob": "missing#;%-*.json"}])))


def test_absolute_glob_obeys_absolute_path_policy(tmp_path: Path) -> None:
    (tmp_path / "data-a.json").write_text("1", encoding="utf-8")
    main = _main(tmp_path, _many([{"glob": str(tmp_path / "data-*.json")}]))
    assert load_yaml_file(main) == [1]
    with pytest.raises(yaml.YAMLError, match="relative paths"):
        load_yaml_file(main, allow_absolute_includes=False)


def test_glob_parent_escape_is_rejected(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (tmp_path / "outside.json").write_text("1", encoding="utf-8")
    with pytest.raises(yaml.YAMLError, match=r"outside|root|parent"):
        load_yaml_file(_main(allowed, _many([{"glob": "../*.json"}])), allowed_roots=[allowed])


def test_glob_symlink_file_outside_root_errors(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("1", encoding="utf-8")
    _symlink(allowed / "data-link.json", outside)
    with pytest.raises(yaml.YAMLError, match=r"outside|root"):
        load_yaml_file(_main(allowed, _many([{"glob": "data-*.json"}])), allowed_roots=[allowed])


def test_wildcard_directory_traversal_does_not_follow_symlinks(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    actual = allowed / "actual"
    actual.mkdir()
    (actual / "data.json").write_text("1", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data.json").write_text("2", encoding="utf-8")
    _symlink(allowed / "inside-link", actual, directory=True)
    _symlink(allowed / "outside-link", outside, directory=True)
    assert load_yaml_file(_main(allowed, _many([{"glob": "*/data.json"}])), allowed_roots=[allowed]) == [1]


@pytest.mark.parametrize("outside", [False, True])
def test_fixed_symlink_prefix_is_canonicalized_and_confined(tmp_path: Path, outside: bool) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    target = (tmp_path if outside else allowed) / "target"
    target.mkdir()
    (target / "data.json").write_text("1", encoding="utf-8")
    _symlink(allowed / "fixed", target, directory=True)
    main = _main(allowed, _many([{"glob": "fixed/*.json"}]))
    if outside:
        with pytest.raises(yaml.YAMLError, match=r"outside|root"):
            load_yaml_file(main, allowed_roots=[allowed])
    else:
        assert load_yaml_file(main, allowed_roots=[allowed]) == [1]


def test_custom_resolver_never_falls_back_to_filesystem_globbing(tmp_path: Path) -> None:
    (tmp_path / "data-a.json").write_text("1", encoding="utf-8")
    root = MemoryRoot("custom-many")

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            raise AssertionError("glob reached custom resolver")

    with pytest.raises(yaml.YAMLError, match=r"filesystem|glob|resolver"):
        load_yaml_compose(StringIO(_many([{"glob": "data-*.json"}])), allowed_roots=[root], include_resolver=Resolver())


@pytest.mark.parametrize(
    "text",
    [
        "!include [a.json]",
        "!include {sources: a.json}",
        "!include {sources: [a.json, {path: b.json, extra: 1}]}",
        "!include {sources: [a.json, {glob: '*.json', path: a.json}]}",
        "!include {sources: [a.json, !include b.json]}",
        "!include {sources: [a.json], json_pointer: '#/value'}",
        "!include {sources: [a.json], documents: false}",
        "!include {sources: [a.json], allowed_roots: ['/']}",
    ],
)
def test_invalid_many_spec_fails_before_any_source_is_resolved(text: str) -> None:
    root = MemoryRoot("validation-many")

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            raise AssertionError("invalid include-many specification reached resolver")

    with pytest.raises(yaml.YAMLError):
        load_yaml_compose(StringIO(text), allowed_roots=[root], include_resolver=Resolver())


def test_literal_many_custom_sources_close_and_keep_request_spelling() -> None:
    root = MemoryRoot("requests-many")
    seen: list[str] = []
    streams: list[BytesIO] = []

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            seen.append(request.name)
            stream = BytesIO(b'{"selected":[1,2]}')
            streams.append(stream)
            return ResolvedInclude(MemorySource(root, PurePosixPath("canonical.json")), lambda: stream)

    sources: list[object] = ["alias#;%20.json", {"path": "alias#;%20.json"}]
    assert load_yaml_compose(
        StringIO(_many(sources, json_pointer="/selected")), allowed_roots=[root], include_resolver=Resolver()
    ) == [[1, 2], [1, 2]]
    assert seen == ["alias#;%20.json", "alias#;%20.json"]
    assert all(stream.closed for stream in streams)


def test_zero_glob_budget_does_not_affect_literal_many_paths(tmp_path: Path) -> None:
    (tmp_path / "data.json").write_text("1", encoding="utf-8")
    assert load_yaml_file(_main(tmp_path, _many(["data.json"])), include_limits=IncludeLimits(max_glob_steps=0)) == [1]
    with pytest.raises(yaml.YAMLError, match="glob"):
        load_yaml_file(_main(tmp_path, _many([{"glob": "data*.json"}])), include_limits=IncludeLimits(max_glob_steps=0))


@pytest.mark.skipif(os.name != "nt", reason="Native Windows path separators and drives only; no UNC source is opened")
@pytest.mark.parametrize("absolute", [False, True])
def test_native_windows_many_backslashes(tmp_path: Path, absolute: bool) -> None:
    folder = tmp_path / "folder#;%20"
    folder.mkdir()
    (folder / "data-a.json").write_text("1", encoding="utf-8")
    prefix = folder if absolute else Path(folder.name)
    pattern = str(prefix / "data-*.json")
    assert "\\" in pattern
    assert load_yaml_file(_main(tmp_path, _many([{"glob": pattern}]))) == [1]


def test_recursive_glob_deduplicates_each_spelling_within_one_pattern(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (tmp_path / "data-root.json").write_text("1", encoding="utf-8")
    (nested / "data-child.json").write_text("2", encoding="utf-8")
    assert load_yaml_file(_main(tmp_path, _many([{"glob": "**/**/data-*.json"}]))) == [1, 2]


def test_glob_hidden_names_and_exact_pattern(tmp_path: Path) -> None:
    (tmp_path / ".data.json").write_text("1", encoding="utf-8")
    (tmp_path / "data.json").write_text("2", encoding="utf-8")
    assert load_yaml_file(_main(tmp_path, _many([{"glob": "*.json"}]))) == [1, 2]
    assert load_yaml_file(_main(tmp_path, _many([{"glob": "data.json"}]))) == [2]


def test_glob_symlink_spelling_is_not_deduplicated_by_canonical_identity(tmp_path: Path) -> None:
    target = tmp_path / "data-target.json"
    target.write_text("1", encoding="utf-8")
    _symlink(tmp_path / "data-alias.json", target)
    assert load_yaml_file(_main(tmp_path, _many([{"glob": "data-*.json"}]))) == [1, 1]


def test_parent_segment_after_wildcard_is_rejected_without_traversal(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "data.json").write_text("1", encoding="utf-8")
    with pytest.raises(yaml.YAMLError, match=r"parent|\.\."):
        load_yaml_file(_main(tmp_path, _many([{"glob": "*/../data.json"}])))


def test_broad_ancestor_glob_prefix_cannot_scan_toward_allowed_root(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "data.json").write_text("1", encoding="utf-8")
    pattern = str(tmp_path / "*" / "data.json")
    with pytest.raises(yaml.YAMLError, match="root"):
        load_yaml_file(_main(allowed, _many([{"glob": pattern}])), allowed_roots=[allowed])


def test_recursive_glob_ignores_matching_symlink_directory_leaves(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "data.json").write_text("1", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.json").write_text("2", encoding="utf-8")
    # Its name matches the leaf glob, but it is a directory, not an included file.
    _symlink(allowed / "directory.json", outside, directory=True)
    assert load_yaml_file(_main(allowed, _many([{"glob": "**/*.json"}])), allowed_roots=[allowed]) == [1]


def test_many_path_validation_error_identifies_failing_entry_before_resolving() -> None:
    root = MemoryRoot("many-invalid-route")
    bad_path = "https://example.invalid/bad.yaml"

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            raise AssertionError("path validation must finish before any resolution")

    with pytest.raises(yaml.YAMLError) as caught:
        load_yaml_compose(StringIO(_many(["valid.yaml", bad_path])), allowed_roots=[root], include_resolver=Resolver())
    assert get_include_route(caught.value)[-1].requested_path == bad_path
    assert bad_path in str(caught.value)


@pytest.mark.skipif(
    os.name != "nt", reason="Native Windows anchor parsing only; glob walker is mocked, no filesystem IO"
)
@pytest.mark.parametrize(
    ("pattern", "prefix"),
    [
        (r"\\?\C:\configs\*.json", r"\\?\C:\configs"),
        (r"\\?\UNC\server\share\configs\*.json", r"\\?\UNC\server\share\configs"),
        (r"\\server\share\configs\*.json", r"\\server\share\configs"),
    ],
)
def test_native_windows_glob_anchor_is_not_wildcard_or_cwd(
    monkeypatch: pytest.MonkeyPatch, pattern: str, prefix: str
) -> None:
    captured: list[tuple[Path, tuple[str, ...]]] = []

    def capture(
        base: Path, parts: tuple[str, ...], roots: tuple[Path, ...], step: Callable[[], None], max_matches: int | None
    ) -> list[Path]:
        captured.append((base, parts))
        return [base / "result.json"]

    # Do not open a device path, drive, or UNC share: only the lexical prefix is tested.
    monkeypatch.setattr("yaml_compose._glob._matches_at", capture)
    request = IncludeRequest(
        name=pattern,
        including=None,
        search_paths=(),
        allowed_roots=(FileRoot(Path(prefix)),),
        include_search="local-first",
    )
    assert expand_include_glob(request, step=lambda: None, max_matches=None) == [Path(prefix) / "result.json"]
    assert captured == [(Path(prefix), ("*.json",))]
    assert captured[0][0].is_absolute()
