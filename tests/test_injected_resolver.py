from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO, StringIO
from pathlib import Path, PurePosixPath

import pytest
import yaml

from yaml_compose import (
    UNSET,
    FileSource,
    IncludeLimits,
    IncludeRequest,
    MemoryIncludeResolver,
    MemoryRoot,
    MemorySource,
    ResolvedInclude,
    UnsetType,
    YamlConfigLoader,
    get_default_include_limits,
    load_yaml_compose,
    set_default_include_limits,
)


class TrackingBytesIO(BytesIO):
    def __init__(self, content: bytes, *, fail_read: bool = False) -> None:
        super().__init__(content)
        self.fail_read = fail_read

    def read(self, size: int | None = -1) -> bytes:
        if self.fail_read:
            raise OSError("injected read failure")
        return super().read(size)


class TrackingResolver:
    def __init__(self, root: MemoryRoot, content: bytes, *, fail_read: bool = False) -> None:
        self.root = root
        self.content = content
        self.fail_read = fail_read
        self.streams: list[TrackingBytesIO] = []

    def resolve(self, request: IncludeRequest) -> ResolvedInclude:
        if self.root not in request.allowed_roots:
            raise FileNotFoundError("fixture root is not allowed")
        source = MemorySource(self.root, PurePosixPath("base.yaml"))

        def open_binary() -> TrackingBytesIO:
            stream = TrackingBytesIO(self.content, fail_read=self.fail_read)
            self.streams.append(stream)
            return stream

        return ResolvedInclude(source, open_binary)


class TestInjectedResolver:
    root: MemoryRoot

    @pytest.fixture(autouse=True)
    def _root(self) -> None:
        self.root = MemoryRoot("test-fixtures")

    def loader(
        self,
        files: Mapping[str, str | bytes],
        *,
        max_include_depth: int | UnsetType | None = UNSET,
        max_include_files: int | UnsetType | None = UNSET,
        max_file_bytes: int | UnsetType | None = UNSET,
        max_total_include_bytes: int | UnsetType | None = UNSET,
    ) -> YamlConfigLoader:
        return YamlConfigLoader(
            include_resolver=MemoryIncludeResolver(self.root, files),
            allowed_roots=[self.root],
            max_include_depth=max_include_depth,
            max_include_files=max_include_files,
            max_file_bytes=max_file_bytes,
            max_total_include_bytes=max_total_include_bytes,
        )

    def main_source(self) -> MemorySource:
        return MemorySource(self.root, PurePosixPath("main.yaml"))

    def test_in_memory_nested_relative_include_and_overlay(self) -> None:
        loader = self.loader(
            {
                "env/base.yaml": "service: !include nested/service.yaml\n",
                "env/nested/service.yaml": "host: localhost\nport: 8000\n",
            }
        )
        result = loader.load_stream(
            StringIO(
                "%LOADER YamlCompose\n---\n<<<: !include env/base.yaml\nservice:\n  port: 8081\n"
            ),
            source=self.main_source(),
        )
        assert result == {"service": {"host": "localhost", "port": 8081}}

    def test_memory_cycle_detection_uses_normalized_source_identity(self) -> None:
        loader = self.loader(
            {
                "a.yaml": "value: !include b.yaml\n",
                "b.yaml": "value: !include sub/../a.yaml\n",
            }
        )
        with pytest.raises(yaml.YAMLError, match="include cycle detected"):
            loader.load_include(StringIO("value: !include a.yaml\n"), source=self.main_source())

    def test_memory_resolver_rejects_root_escape_and_missing_include(self) -> None:
        loader = self.loader({"base.yaml": "ok: true\n"})
        with pytest.raises(yaml.YAMLError, match="escapes its namespace root"):
            loader.load_include(
                StringIO("value: !include ../secret.yaml\n"), source=self.main_source()
            )
        with pytest.raises(yaml.YAMLError, match="was not found"):
            loader.load_include(
                StringIO("value: !include absent.yaml\n"), source=self.main_source()
            )
        with pytest.raises(yaml.YAMLError, match="URI or drive paths"):
            loader.load_include(
                StringIO("value: !include https://example.invalid/base.yaml\n"),
                source=self.main_source(),
            )
        with pytest.raises(yaml.YAMLError, match="relative"):
            loader.load_include(
                StringIO("value: !include /base.yaml\n"),
                source=self.main_source(),
            )

    def test_memory_limits_apply_to_injected_streams(self) -> None:
        content = "key: long-value\n"
        with pytest.raises(yaml.YAMLError, match="per-file size limit"):
            self.loader({"base.yaml": content}, max_file_bytes=4).load_include(
                StringIO("value: !include base.yaml\n"), source=self.main_source()
            )
        with pytest.raises(yaml.YAMLError, match="maximum include depth"):
            self.loader({"base.yaml": content}, max_include_depth=0).load_include(
                StringIO("value: !include base.yaml\n"), source=self.main_source()
            )
        with pytest.raises(yaml.YAMLError, match="maximum include count"):
            self.loader({"base.yaml": content}, max_include_files=0).load_include(
                StringIO("value: !include base.yaml\n"), source=self.main_source()
            )
        with pytest.raises(yaml.YAMLError, match="maximum total included bytes"):
            self.loader({"base.yaml": content}, max_total_include_bytes=3).load_include(
                StringIO("value: !include base.yaml\n"), source=self.main_source()
            )
        with pytest.raises(yaml.YAMLError, match="maximum total included bytes"):
            self.loader(
                {"first.yaml": "a: 1\n", "second.yaml": "b: 2\n"},
                max_total_include_bytes=8,
            ).load_include(
                StringIO("first: !include first.yaml\nsecond: !include second.yaml\n"),
                source=self.main_source(),
            )
        with pytest.raises(yaml.YAMLError, match="maximum include depth"):
            load_yaml_compose(
                StringIO("value: !include first.yaml\n"),
                allowed_roots=[self.root],
                include_resolver=MemoryIncludeResolver(
                    self.root,
                    {
                        "first.yaml": "value: !include second.yaml\n",
                        "second.yaml": "value: done\n",
                    },
                ),
                max_include_depth=1,
            )

    def test_none_disables_all_limits_while_omission_inherits_defaults(self) -> None:
        files = {
            "first.yaml": "next: !include second.yaml\n",
            "second.yaml": "value: long-value\n",
        }
        resolver = MemoryIncludeResolver(self.root, files)
        document = "value: !include first.yaml\n"
        original = get_default_include_limits()
        try:
            set_default_include_limits(
                IncludeLimits(
                    max_include_depth=0,
                    max_include_files=0,
                    max_file_bytes=0,
                    max_total_include_bytes=0,
                )
            )
            with pytest.raises(yaml.YAMLError, match="maximum include depth"):
                load_yaml_compose(
                    StringIO(document),
                    source=self.main_source(),
                    allowed_roots=[self.root],
                    include_resolver=resolver,
                )

            expected = {"value": {"next": {"value": "long-value"}}}
            assert (
                load_yaml_compose(
                    StringIO(document),
                    source=self.main_source(),
                    allowed_roots=[self.root],
                    include_resolver=resolver,
                    max_include_depth=None,
                    max_include_files=None,
                    max_file_bytes=None,
                    max_total_include_bytes=None,
                )
                == expected
            )
            loader = YamlConfigLoader(
                include_resolver=resolver,
                allowed_roots=[self.root],
                max_include_depth=None,
                max_include_files=None,
                max_file_bytes=None,
                max_total_include_bytes=None,
            )
            assert loader.max_include_depth is None
            assert loader.max_include_files is None
            assert loader.max_file_bytes is None
            assert loader.max_total_include_bytes is None
            assert loader.load_include(StringIO(document), source=self.main_source()) == expected
        finally:
            set_default_include_limits(original)

    def test_unbounded_byte_limits_do_not_disable_the_other_bound(self) -> None:
        content = "value: long-value\n"
        with pytest.raises(yaml.YAMLError, match="maximum total included bytes"):
            self.loader(
                {"base.yaml": content},
                max_file_bytes=None,
                max_total_include_bytes=3,
            ).load_include(StringIO("value: !include base.yaml\n"), source=self.main_source())
        with pytest.raises(yaml.YAMLError, match="per-file size limit"):
            self.loader(
                {"base.yaml": content},
                max_file_bytes=3,
                max_total_include_bytes=None,
            ).load_include(StringIO("value: !include base.yaml\n"), source=self.main_source())

    def test_injected_stream_is_closed_after_read_and_on_read_error(self) -> None:
        root = self.root
        source = self.main_source()
        resolver = TrackingResolver(root, b"value: [\n")
        with pytest.raises(yaml.YAMLError):
            load_yaml_compose(
                StringIO("value: !include base.yaml\n"),
                source=source,
                allowed_roots=[root],
                include_resolver=resolver,
            )
        assert len(resolver.streams) == 1
        assert resolver.streams[0].closed

        oversized = TrackingResolver(root, b"value: too large\n")
        with pytest.raises(yaml.YAMLError, match="per-file size limit"):
            load_yaml_compose(
                StringIO("value: !include base.yaml\n"),
                source=source,
                allowed_roots=[root],
                include_resolver=oversized,
                max_file_bytes=2,
            )
        assert oversized.streams[0].closed

        failing = TrackingResolver(root, b"ignored", fail_read=True)
        with pytest.raises(yaml.YAMLError, match="injected read failure"):
            load_yaml_compose(
                StringIO("value: !include base.yaml\n"),
                source=source,
                allowed_roots=[root],
                include_resolver=failing,
            )
        assert failing.streams[0].closed

    def test_duplicate_keys_and_python_tags_stay_rejected_with_memory_resolver(self) -> None:
        duplicate = self.loader({"base.yaml": "value: first\nvalue: second\n"})
        with pytest.raises(yaml.YAMLError, match="duplicate key"):
            duplicate.load_include(StringIO("<<<: !include base.yaml\n"), source=self.main_source())

        unsafe = self.loader(
            {"base.yaml": 'value: !!python/object/apply:os.system ["echo unsafe"]\n'}
        )
        with pytest.raises(yaml.YAMLError):
            unsafe.load_include(StringIO("<<<: !include base.yaml\n"), source=self.main_source())

    def test_reused_configured_loader_gets_a_fresh_budget_per_call(self) -> None:
        loader = self.loader({"base.yaml": "value: 1\n"}, max_include_files=1)
        for _ in range(2):
            assert loader.load_include(
                StringIO("value: !include base.yaml\n"), source=self.main_source()
            ) == {"value": {"value": 1}}

    def test_reused_configured_loader_preserves_default_filesystem_lookup(
        self, test_path: Path
    ) -> None:
        (test_path / "base.yaml").write_text("value: 1\n", encoding="utf-8")
        config = test_path / "main.yaml"
        config.write_text("%LOADER YamlCompose\n---\n<<<: !include base.yaml\n", encoding="utf-8")
        loader = YamlConfigLoader(max_include_files=1)
        assert loader.load_file(config) == {"value": 1}
        assert loader.load_file(config) == {"value": 1}

    def test_memory_source_cannot_be_used_with_an_unconfigured_filesystem_resolver(self) -> None:
        loader = YamlConfigLoader(allowed_roots=[self.root])
        with pytest.raises(
            yaml.YAMLError, match="filesystem resolver cannot resolve relative to a memory source"
        ):
            loader.load_include(StringIO("value: !include base.yaml\n"), source=self.main_source())

    def test_custom_resolver_cannot_escape_file_roots_before_opening(self, test_path: Path) -> None:
        inside = test_path / "inside"
        outside = test_path / "outside"
        inside.mkdir()
        outside.mkdir()
        main = inside / "main.yaml"
        main.write_text("value: !include base.yaml\n", encoding="utf-8")
        escaped = outside / "base.yaml"
        escaped.write_text("outside: true\n", encoding="utf-8")

        class EscapingResolver:
            opened = False

            def resolve(self, request: IncludeRequest) -> ResolvedInclude:
                def open_binary() -> BytesIO:
                    self.opened = True
                    return BytesIO(b"outside: true\n")

                return ResolvedInclude(FileSource(escaped), open_binary)

        resolver = EscapingResolver()
        with pytest.raises(yaml.YAMLError, match="outside the allowed roots"):
            load_yaml_compose(
                StringIO("value: !include base.yaml\n"),
                source_path=main,
                allowed_roots=[inside],
                include_resolver=resolver,
            )
        assert not resolver.opened
