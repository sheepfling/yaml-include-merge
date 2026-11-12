"""Keep local path text independent of include option and selector syntax.

Native filesystem tests use only names valid on their host. Foreign Windows
paths are tested as inert syntax or native-Windows lexical validation, never
by opening a UNC share or pretending POSIX can open a Windows drive path.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from io import BytesIO, StringIO
from pathlib import Path, PurePosixPath
from typing import cast

import pytest
import yaml

from yaml_compose import (
    IncludeRequest,
    MemoryRoot,
    MemorySource,
    ResolvedInclude,
    get_include_route,
    load_yaml_compose,
    load_yaml_file,
)
from yaml_compose._include_specs import parse_include_spec
from yaml_compose.resolver import validate_include_name

_TAGS = ("!include", "!include-all", "!include-table", "!include-text", "!include-raw")
_PAYLOADS: dict[str, tuple[str, bytes, object]] = {
    "!include": ("yaml", b"value: selected\n", {"value": "selected"}),
    "!include-all": ("yaml", b"---\none\n---\ntwo\n", ["one", "two"]),
    "!include-table": ("csv", b"name,value\na,1\n", [["name", "value"], ["a", "1"]]),
    "!include-text": ("txt", b"one\r\ntwo\n", "one\r\ntwo\n"),
    "!include-raw": ("bin", b"\x00\xffone\n", b"\x00\xffone\n"),
}
_PORTABLE_NAMES = (
    "plain",
    "with spaces",
    "hash#part",
    "semi;colon",
    "percent%20literal",
    "percent%23hash",
    "apostrophe's",
    "plus+equals=comma,",
    "snow-雪",
    "mixed#;%20 quote'",
)


def _quote(value: str, style: str) -> str:
    if style == "single":
        return "'" + value.replace("'", "''") + "'"
    return json.dumps(value, ensure_ascii=False)


def _include(tag: str, path: str, *, mapping: bool, style: str = "double") -> str:
    quoted = _quote(path, style)
    return f"{tag} {{path: {quoted}}}" if mapping else f"{tag} {quoted}"


@pytest.mark.parametrize("tag", _TAGS)
@pytest.mark.parametrize("mapping", [False, True], ids=["scalar", "mapping"])
@pytest.mark.parametrize("style", ["single", "double"])
@pytest.mark.parametrize("stem", _PORTABLE_NAMES)
def test_native_literal_path_punctuation_and_yaml_quoting(
    tmp_path: Path, tag: str, mapping: bool, style: str, stem: str
) -> None:
    suffix, content, expected = _PAYLOADS[tag]
    name = f"{stem}.{suffix}"
    (tmp_path / name).write_bytes(content)
    main = tmp_path / "main.yaml"
    main.write_text("%LOADER YamlCompose\n---\n" + _include(tag, name, mapping=mapping, style=style), encoding="utf-8")
    assert load_yaml_file(main) == expected


@pytest.mark.skipif(os.name == "nt", reason="These are POSIX filenames, not Windows filename or ADS tests")
@pytest.mark.parametrize("tag", _TAGS)
@pytest.mark.parametrize("mapping", [False, True], ids=["scalar", "mapping"])
@pytest.mark.parametrize("stem", ['double"quote', r"back\slash", "colon:name", "question?star*pipe|", "<angle>"])
def test_native_posix_filename_characters(tmp_path: Path, tag: str, mapping: bool, stem: str) -> None:
    suffix, content, expected = _PAYLOADS[tag]
    name = f"{stem}.{suffix}"
    (tmp_path / name).write_bytes(content)
    main = tmp_path / "main.yaml"
    # ./ makes colon-containing names explicitly relative instead of scheme-like.
    main.write_text("%LOADER YamlCompose\n---\n" + _include(tag, "./" + name, mapping=mapping), encoding="utf-8")
    assert load_yaml_file(main) == expected


@pytest.mark.parametrize("tag", _TAGS)
@pytest.mark.parametrize("mapping", [False, True], ids=["scalar", "mapping"])
def test_native_absolute_path_and_policy(tmp_path: Path, tag: str, mapping: bool) -> None:
    suffix, content, expected = _PAYLOADS[tag]
    source = tmp_path / f"absolute#; %23.{suffix}"
    source.write_bytes(content)
    text = _include(tag, str(source), mapping=mapping)
    assert load_yaml_compose(StringIO(text), allowed_roots=[tmp_path]) == expected
    with pytest.raises(yaml.YAMLError, match="relative paths"):
        load_yaml_compose(StringIO(text), allowed_roots=[tmp_path], allow_absolute_includes=False)


@pytest.mark.parametrize("tag", _TAGS)
@pytest.mark.parametrize("mapping", [False, True], ids=["scalar", "mapping"])
@pytest.mark.parametrize("outer_mapping", [False, True], ids=["outer-scalar", "outer-mapping"])
def test_nested_paths_resolve_beside_containing_file(
    tmp_path: Path, tag: str, mapping: bool, outer_mapping: bool
) -> None:
    suffix, content, expected = _PAYLOADS[tag]
    directory = tmp_path / "nested #;%20 space"
    directory.mkdir()
    leaf = f"leaf#%23; with space.{suffix}"
    (directory / leaf).write_bytes(content)
    # A misleading same-named root file must never replace the local nested file.
    (tmp_path / leaf).write_bytes(b"wrong")
    nested = directory / "outer#;%.yaml"
    nested.write_text("value: " + _include(tag, "./" + leaf, mapping=mapping), encoding="utf-8")
    main = tmp_path / "main.yaml"
    main.write_text(
        "%LOADER YamlCompose\n---\n"
        + _include("!include", nested.relative_to(tmp_path).as_posix(), mapping=outer_mapping),
        encoding="utf-8",
    )
    assert load_yaml_file(main) == {"value": expected}


@pytest.mark.parametrize("tag", _TAGS)
@pytest.mark.parametrize("mapping", [False, True], ids=["scalar", "mapping"])
def test_resolver_receives_literal_name_and_controls_source_identity(tag: str, mapping: bool) -> None:
    suffix, content, expected = _PAYLOADS[tag]
    root = MemoryRoot("path-contract")
    requested = r"directory/alias#;%20's\piece" + f".{suffix}"
    canonical = f"resolved#source.{suffix}"
    seen: list[IncludeRequest] = []
    streams: list[BytesIO] = []

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            seen.append(request)

            def open_binary() -> BytesIO:
                stream = BytesIO(content)
                streams.append(stream)
                return stream

            return ResolvedInclude(MemorySource(root, PurePosixPath(canonical)), open_binary)

    assert (
        load_yaml_compose(
            StringIO(_include(tag, requested, mapping=mapping)), allowed_roots=[root], include_resolver=Resolver()
        )
        == expected
    )
    assert len(seen) == 1
    assert seen[0].name == requested
    assert len(streams) == 1 and streams[0].closed


@pytest.mark.parametrize("tag", ["!include", "!include-all"])
@pytest.mark.parametrize("mapping", [False, True], ids=["scalar", "mapping"])
def test_alias_spelling_does_not_change_canonical_cycle_identity(tag: str, mapping: bool) -> None:
    root = MemoryRoot("identity")
    source = MemorySource(root, PurePosixPath("canonical#;%.yaml"))
    opened = False

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            assert request.name == "different#alias%23.yaml"

            def open_binary() -> BytesIO:
                nonlocal opened
                opened = True
                return BytesIO(b"value: 1")

            return ResolvedInclude(source, open_binary)

    with pytest.raises(yaml.YAMLError, match="include cycle detected") as caught:
        load_yaml_compose(
            StringIO(_include(tag, "different#alias%23.yaml", mapping=mapping)),
            source=source,
            allowed_roots=[root],
            include_resolver=Resolver(),
        )
    assert not opened
    route = get_include_route(caught.value)
    assert route[-1].requested_path == "different#alias%23.yaml"
    assert route[-1].resolved_source == "memory:identity/canonical#;%.yaml"


@pytest.mark.parametrize("tag", _TAGS)
@pytest.mark.parametrize("mapping", [False, True], ids=["scalar", "mapping"])
@pytest.mark.parametrize("path", ["https://example.invalid/a#b", "file:///tmp/a.yaml", "foo:bar.yaml", "bad\x00name"])
def test_invalid_path_policy_fails_before_resolver(tag: str, mapping: bool, path: str) -> None:
    root = MemoryRoot("rejected")

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            raise AssertionError("invalid local path reached resolver")

    with pytest.raises(yaml.YAMLError):
        load_yaml_compose(
            StringIO(_include(tag, path, mapping=mapping)), allowed_roots=[root], include_resolver=Resolver()
        )


@pytest.mark.parametrize("tag", _TAGS)
@pytest.mark.parametrize("mapping", [False, True], ids=["scalar", "mapping"])
@pytest.mark.parametrize("style", ["single", "double"])
@pytest.mark.parametrize(
    "path",
    [
        r"C:\configs\name#;%20.yaml",
        "C:/configs/name#;%20.yaml",
        r"C:relative.yaml",
        r"\\server\share\name#;%20.yaml",
        r"\rooted\name.yaml",
        "/etc/name#;%20.yaml",
    ],
)
def test_option_parser_preserves_foreign_path_text_without_resolving(
    tag: str, mapping: bool, style: str, path: str
) -> None:
    # This is syntax-only: no resolver, file, drive, or network share is opened.
    compose = cast(Callable[[str], yaml.Node | None], yaml.compose)  # pyright: ignore[reportUnknownMemberType]
    node = compose(_include(tag, path, mapping=mapping, style=style))
    assert node is not None
    if mapping:
        assert parse_include_spec(node, tag=tag).path == path
    else:
        assert isinstance(node, yaml.ScalarNode)
        assert node.value == path


@pytest.mark.skipif(os.name != "nt", reason="Native Windows path-policy checks only; no UNC source is opened")
@pytest.mark.parametrize("path", [r"C:\configs\name#;%.yaml", "C:/configs/name.yaml", r"\\server\share\name#;%.yaml"])
def test_native_windows_drive_and_unc_lexical_policy(path: str) -> None:
    assert Path(path).is_absolute()
    validate_include_name(path, allow_absolute=True)
    with pytest.raises(ValueError, match="relative paths"):
        validate_include_name(path, allow_absolute=False)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows path-policy checks only")
@pytest.mark.parametrize("path", [r"C:relative.yaml", r"\rooted.yaml", "/rooted.yaml"])
def test_native_windows_incomplete_absolute_lexical_policy(path: str) -> None:
    for allow_absolute in (False, True):
        with pytest.raises(ValueError):
            validate_include_name(path, allow_absolute=allow_absolute)


@pytest.mark.skipif(os.name == "nt", reason="Native POSIX path-policy checks only")
def test_native_posix_lexical_policy() -> None:
    validate_include_name("/tmp/name#;%20.yaml", allow_absolute=True)
    validate_include_name("./colon:name#;%.yaml")
    validate_include_name(r"back\slash#;%.yaml")
    with pytest.raises(ValueError, match="relative paths"):
        validate_include_name("/tmp/name.yaml")
    with pytest.raises(ValueError, match="local paths"):
        validate_include_name(r"C:\foreign\name.yaml", allow_absolute=True)


@pytest.mark.parametrize("tag", _TAGS)
@pytest.mark.parametrize("mapping", [False, True], ids=["scalar", "mapping"])
def test_selector_looking_path_remains_literal_even_if_prefix_file_exists(
    tmp_path: Path, tag: str, mapping: bool
) -> None:
    suffix, content, expected = _PAYLOADS[tag]
    prefix = f"base.{suffix}"
    (tmp_path / prefix).write_bytes(b"wrong")
    directory = tmp_path / (prefix + "#")
    directory.mkdir()
    name = f"{prefix}#/node.{suffix}"
    (tmp_path / name).write_bytes(content)
    main = tmp_path / "main.yaml"
    main.write_text("%LOADER YamlCompose\n---\n" + _include(tag, name, mapping=mapping), encoding="utf-8")
    assert load_yaml_file(main) == expected


@pytest.mark.parametrize("tag", _TAGS)
@pytest.mark.parametrize("mapping", [False, True], ids=["scalar", "mapping"])
def test_percent_name_is_not_decoded_when_decoded_name_also_exists(tmp_path: Path, tag: str, mapping: bool) -> None:
    suffix, content, expected = _PAYLOADS[tag]
    name = f"space%20name.{suffix}"
    (tmp_path / name).write_bytes(content)
    (tmp_path / f"space name.{suffix}").write_bytes(b"wrong")
    main = tmp_path / "main.yaml"
    main.write_text("%LOADER YamlCompose\n---\n" + _include(tag, name, mapping=mapping), encoding="utf-8")
    assert load_yaml_file(main) == expected


@pytest.mark.skipif(os.name != "nt", reason="Native Windows backslash-separated paths only")
@pytest.mark.parametrize("tag", _TAGS)
@pytest.mark.parametrize("mapping", [False, True], ids=["scalar", "mapping"])
@pytest.mark.parametrize("style", ["single", "double"])
def test_native_windows_backslash_relative_path(tmp_path: Path, tag: str, mapping: bool, style: str) -> None:
    suffix, content, expected = _PAYLOADS[tag]
    directory = tmp_path / "nested#;% space"
    directory.mkdir()
    leaf = f"leaf#;%23.{suffix}"
    (directory / leaf).write_bytes(content)
    name = str(Path(directory.name) / leaf)
    assert "\\" in name
    main = tmp_path / "main.yaml"
    main.write_text("%LOADER YamlCompose\n---\n" + _include(tag, name, mapping=mapping, style=style), encoding="utf-8")
    assert load_yaml_file(main) == expected


@pytest.mark.parametrize("tag", _TAGS)
@pytest.mark.parametrize("mapping", [False, True], ids=["scalar", "mapping"])
def test_punctuation_does_not_bypass_parent_path_confinement(tmp_path: Path, tag: str, mapping: bool) -> None:
    suffix, content, _expected = _PAYLOADS[tag]
    outside = f"outside#;%20.{suffix}"
    (tmp_path / outside).write_bytes(content)
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    main = allowed / "main.yaml"
    main.write_text("%LOADER YamlCompose\n---\n" + _include(tag, "../" + outside, mapping=mapping), encoding="utf-8")
    with pytest.raises(yaml.YAMLError, match="outside the allowed roots"):
        load_yaml_file(main, allowed_roots=[allowed])
