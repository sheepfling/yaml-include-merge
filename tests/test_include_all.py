from __future__ import annotations

from io import BytesIO, StringIO
from pathlib import Path, PurePosixPath

import pytest
import yaml

from yaml_compose import (
    INCLUDE_ALL_TAG,
    IncludeLimits,
    IncludeRequest,
    MemoryIncludeResolver,
    MemoryRoot,
    MemorySource,
    ResolvedInclude,
    YamlConfigLoader,
    get_include_route,
    load_yaml_compose,
    load_yaml_file,
)


def load_stream_include(content: str, *, pointer: str | None = None, recursive: bool = True) -> object:
    root = MemoryRoot("stream")
    spec = (
        "!include-all bundle.yaml"
        if pointer is None
        else f'!include-all {{path: bundle.yaml, json_pointer: "{pointer}"}}'
    )
    return load_yaml_compose(
        StringIO(spec),
        allowed_roots=[root],
        include_resolver=MemoryIncludeResolver(root, {"bundle.yaml": content}),
        recursive_dispatch=recursive,
    )


@pytest.mark.parametrize("recursive", [True, False])
@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("", []),
        ("# comment\n", []),
        ("---\n", [None]),
        ("---\n...\n---\n", [None, None]),
        ("value: 1\n", [{"value": 1}]),
        ("[one, two]\n", [["one", "two"]]),
        ("---\nvalue: 1\n---\nvalue: 2\n", [{"value": 1}, {"value": 2}]),
        ("---\n[one]\n---\nscalar\n---\nnull\n", [["one"], "scalar", None]),
        ("%LOADER YamlCompose\n---\nvalue: 1\n---\nvalue: 2\n", [{"value": 1}, {"value": 2}]),
    ],
)
def test_all_documents_always_returns_list(content: str, expected: object, recursive: bool) -> None:
    assert INCLUDE_ALL_TAG == "!include-all"
    assert load_stream_include(content, recursive=recursive) == expected
    assert load_stream_include(content, pointer="", recursive=recursive) == expected


@pytest.mark.parametrize("recursive", [True, False])
def test_pointer_selects_from_document_list_without_flattening(recursive: bool) -> None:
    content = "---\n[zero, one]\n---\nsettings: {port: 8080}\n"
    assert load_stream_include(content, pointer="/0/1", recursive=recursive) == "one"
    assert load_stream_include(content, pointer="/1/settings", recursive=recursive) == {"port": 8080}
    with pytest.raises(yaml.YAMLError, match="out of bounds"):
        load_stream_include(content, pointer="/2", recursive=recursive)
    with pytest.raises(yaml.YAMLError, match="out of bounds"):
        load_stream_include("", pointer="/0", recursive=recursive)
    assert load_stream_include("---\n", pointer="/0", recursive=recursive) is None


@pytest.mark.parametrize("recursive", [True, False])
def test_anchors_are_document_local(recursive: bool) -> None:
    content = "---\nfirst: &shared {value: one}\nalias: *shared\n---\nsecond: &shared {value: two}\nalias: *shared\n"
    assert load_stream_include(content, pointer="/1/alias/value", recursive=recursive) == "two"
    with pytest.raises(yaml.YAMLError, match="undefined alias"):
        load_stream_include("---\nfirst: &shared 1\n---\nsecond: *shared\n", recursive=recursive)


@pytest.mark.parametrize("recursive", [True, False])
@pytest.mark.parametrize("last", ["broken: [\n", "duplicate: 1\nduplicate: 2\n", "bad: *missing\n"])
def test_late_document_errors_are_not_hidden_by_pointer(last: str, recursive: bool) -> None:
    with pytest.raises(yaml.YAMLError) as caught:
        load_stream_include("---\nselected: 1\n---\n" + last, pointer="/0", recursive=recursive)
    route = get_include_route(caught.value)
    assert route[-1].tag == "!include-all"
    assert route[-1].requested_path == "bundle.yaml"
    assert "bundle.yaml" in str(caught.value)


def test_nested_includes_and_overlays_finish_before_stream_selection() -> None:
    root = MemoryRoot("nested")
    loader = YamlConfigLoader(
        allowed_roots=[root],
        include_resolver=MemoryIncludeResolver(
            root,
            {
                "dir/bundle.yaml": (
                    "---\nignored: !include values.yaml\n---\nsettings:\n  <<<: !include values.yaml\n  port: 8080\n"
                ),
                "dir/values.yaml": "host: localhost\nport: 80\n",
            },
        ),
    )
    assert loader.load_include(
        StringIO('<<<: !include-all {path: "dir/bundle.yaml", json_pointer: "/1/settings"}\nlocal: true\n')
    ) == {
        "host": "localhost",
        "port": 8080,
        "local": True,
    }
    assert loader.load_include(StringIO('docs: [before, !spread [!include-all "dir/bundle.yaml"], after]\n')) == {
        "docs": [
            "before",
            {"ignored": {"host": "localhost", "port": 80}},
            {"settings": {"host": "localhost", "port": 8080}},
            "after",
        ]
    }
    with pytest.raises(yaml.YAMLError, match="must be a mapping") as caught:
        loader.load_include(StringIO('<<<: !include-all "dir/bundle.yaml"\n'))
    assert get_include_route(caught.value)[-1].tag == "!include-all"


@pytest.mark.parametrize("suffix", ["json", "jsonl", "toml", "csv"])
def test_all_documents_requires_yaml_format(suffix: str) -> None:
    root = MemoryRoot("formats")
    with pytest.raises(yaml.YAMLError, match="requires a YAML source"):
        load_yaml_compose(
            StringIO(f'!include-all "data.{suffix}"\n'),
            allowed_roots=[root],
            include_resolver=MemoryIncludeResolver(root, {f"data.{suffix}": ""}),
        )


def test_unknown_suffix_retains_existing_yaml_dispatch() -> None:
    root = MemoryRoot("unknown")
    assert load_yaml_compose(
        StringIO('!include-all "data.config"\n'),
        allowed_roots=[root],
        include_resolver=MemoryIncludeResolver(root, {"data.config": "---\none\n---\ntwo\n"}),
    ) == ["one", "two"]


@pytest.mark.parametrize("recursive", [True, False])
def test_custom_yaml_loader_is_used_for_every_document(recursive: bool) -> None:
    root = MemoryRoot("custom")

    class CustomLoader(yaml.SafeLoader):
        pass

    def construct_custom(loader: yaml.SafeLoader, node: yaml.Node) -> object:
        return {"custom": True}

    CustomLoader.add_constructor("!custom", construct_custom)
    assert load_yaml_compose(
        StringIO('!include-all "bundle.yaml"\n'),
        allowed_roots=[root],
        include_resolver=MemoryIncludeResolver(root, {"bundle.yaml": "---\n!custom one\n---\n!custom two\n"}),
        yaml_loader=CustomLoader,
        recursive_dispatch=recursive,
    ) == [{"custom": True}, {"custom": True}]


def test_nonrecursive_mode_does_not_enable_nested_include_tags() -> None:
    with pytest.raises(yaml.YAMLError, match="could not determine a constructor"):
        load_stream_include("---\none\n---\n!include other.yaml\n", pointer="/0", recursive=False)


@pytest.mark.parametrize(
    "limits",
    [
        IncludeLimits(max_file_bytes=10),
        IncludeLimits(max_total_include_bytes=10),
        IncludeLimits(max_include_files=0),
        IncludeLimits(max_include_depth=0),
    ],
)
def test_shared_limits_cover_whole_stream(limits: IncludeLimits) -> None:
    root = MemoryRoot("budget")
    with pytest.raises(yaml.YAMLError, match=r"maximum|limit"):
        load_yaml_compose(
            StringIO('!include-all {path: "bundle.yaml", json_pointer: "/0"}\n'),
            allowed_roots=[root],
            include_limits=limits,
            include_resolver=MemoryIncludeResolver(root, {"bundle.yaml": "---\n1\n---\nlong-value\n"}),
        )


def test_each_document_is_not_counted_as_a_separate_file() -> None:
    root = MemoryRoot("count")
    content = "---\none\n---\ntwo\n"
    assert load_yaml_compose(
        StringIO('!include-all "bundle.yaml"\n'),
        allowed_roots=[root],
        include_resolver=MemoryIncludeResolver(root, {"bundle.yaml": content}),
        include_limits=IncludeLimits(max_include_files=1, max_include_depth=1, max_total_include_bytes=len(content)),
    ) == ["one", "two"]


def test_nested_reads_in_unselected_documents_share_budget() -> None:
    root = MemoryRoot("nested-budget")
    with pytest.raises(yaml.YAMLError, match="maximum include count"):
        load_yaml_compose(
            StringIO('!include-all {path: "bundle.yaml", json_pointer: "/0"}\n'),
            allowed_roots=[root],
            max_include_files=1,
            include_resolver=MemoryIncludeResolver(
                root,
                {
                    "bundle.yaml": "---\none\n---\n!include child.yaml\n",
                    "child.yaml": "two\n",
                },
            ),
        )


@pytest.mark.parametrize("tag", ["!include", "!include-all"])
def test_recursive_cycle_detection_uses_source_not_selector(tag: str) -> None:
    root = MemoryRoot("cycle")
    with pytest.raises(yaml.YAMLError, match="include cycle detected"):
        load_yaml_compose(
            StringIO('!include-all {path: "bundle.yaml", json_pointer: "/0"}\n'),
            allowed_roots=[root],
            include_resolver=MemoryIncludeResolver(
                root,
                {
                    "bundle.yaml": f'---\none\n---\n{tag} {{path: "bundle.yaml", json_pointer: "/0"}}\n',
                },
            ),
        )


def test_resolver_source_format_and_closed_stream() -> None:
    root = MemoryRoot("resolver")
    stream = BytesIO(b"---\none\n---\ntwo\n")

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            assert request.name == "alias"
            return ResolvedInclude(MemorySource(root, PurePosixPath("opaque")), lambda: stream, format_id="yaml")

    assert (
        load_yaml_compose(
            StringIO('!include-all {path: "alias", json_pointer: "/1"}\n'),
            allowed_roots=[root],
            include_resolver=Resolver(),
        )
        == "two"
    )
    assert stream.closed


def test_filesystem_confines_stream_sources(test_path: Path) -> None:
    allowed = test_path / "allowed"
    allowed.mkdir()
    outside = test_path / "outside.yaml"
    outside.write_text("---\none\n---\ntwo\n", encoding="utf-8")
    (allowed / "link.yaml").symlink_to(outside)
    config = allowed / "main.yaml"
    config.write_text(
        '%LOADER YamlCompose\n---\n!include-all {path: "link.yaml", json_pointer: "/0"}\n', encoding="utf-8"
    )
    with pytest.raises(yaml.YAMLError, match="outside the allowed roots"):
        load_yaml_file(config)


def test_ordinary_include_stays_single_document() -> None:
    root = MemoryRoot("ordinary")
    with pytest.raises(yaml.YAMLError, match="expected a single document"):
        load_yaml_compose(
            StringIO('!include {path: "bundle.yaml", json_pointer: "/0"}\n'),
            allowed_roots=[root],
            include_resolver=MemoryIncludeResolver(root, {"bundle.yaml": "---\none\n---\ntwo\n"}),
        )


@pytest.mark.parametrize("recursive", [True, False])
def test_late_parse_failure_disposes_yaml_loaders_and_closes_source(recursive: bool) -> None:
    root = MemoryRoot("cleanup")
    stream = BytesIO(b"---\nvalid: true\n---\nbroken: [\n")
    disposed_names: list[object] = []

    class CustomLoader(yaml.SafeLoader):
        def dispose(self) -> None:
            disposed_names.append(getattr(self, "name", None))
            super().dispose()  # pyright: ignore[reportUnknownMemberType]

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            return ResolvedInclude(MemorySource(root, PurePosixPath("bundle.yaml")), lambda: stream)

    with pytest.raises(yaml.YAMLError):
        load_yaml_compose(
            StringIO('!include-all {path: "bundle.yaml", json_pointer: "/0"}\n'),
            allowed_roots=[root],
            include_resolver=Resolver(),
            yaml_loader=CustomLoader,
            recursive_dispatch=recursive,
        )
    assert stream.closed
    assert "memory:cleanup/bundle.yaml" in disposed_names
    assert len(disposed_names) == 2


def test_explicit_non_yaml_format_is_rejected_before_source_open() -> None:
    root = MemoryRoot("format-rejection")
    opened = False

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            def open_binary() -> BytesIO:
                nonlocal opened
                opened = True
                return BytesIO(b"{}")

            return ResolvedInclude(MemorySource(root, PurePosixPath("bundle.yaml")), open_binary, format_id="json")

    with pytest.raises(yaml.YAMLError, match="requires a YAML source"):
        load_yaml_compose(
            StringIO('!include-all "bundle.yaml"\n'),
            allowed_roots=[root],
            include_resolver=Resolver(),
        )
    assert not opened
