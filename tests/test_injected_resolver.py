from __future__ import annotations

from io import BytesIO, StringIO
from pathlib import Path, PurePosixPath
import tempfile
import unittest

import yaml

from yaml_include_merge import (
    FileSource,
    IncludeRequest,
    MemoryIncludeResolver,
    MemoryRoot,
    MemorySource,
    ResolvedInclude,
    YamlConfigLoader,
    load_yaml_include,
)


class TrackingBytesIO(BytesIO):
    def __init__(self, content: bytes, *, fail_read: bool = False) -> None:
        super().__init__(content)
        self.fail_read = fail_read

    def read(self, size: int = -1) -> bytes:
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


class InjectedResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = MemoryRoot("test-fixtures")

    def loader(self, files: dict[str, str | bytes], **limits: int) -> YamlConfigLoader:
        return YamlConfigLoader(
            include_resolver=MemoryIncludeResolver(self.root, files),
            allowed_roots=[self.root],
            **limits,
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
                "%LOADER LoadInclude\n---\n"
                "<<: !include env/base.yaml\n"
                "service:\n  port: 8081\n"
            ),
            source=self.main_source(),
        )
        self.assertEqual(result, {"service": {"host": "localhost", "port": 8081}})

    def test_memory_cycle_detection_uses_normalized_source_identity(self) -> None:
        loader = self.loader(
            {
                "a.yaml": "value: !include b.yaml\n",
                "b.yaml": "value: !include sub/../a.yaml\n",
            }
        )
        with self.assertRaisesRegex(yaml.YAMLError, "include cycle detected"):
            loader.load_include(StringIO("value: !include a.yaml\n"), source=self.main_source())

    def test_memory_resolver_rejects_root_escape_and_missing_include(self) -> None:
        loader = self.loader({"base.yaml": "ok: true\n"})
        with self.assertRaisesRegex(yaml.YAMLError, "escapes its namespace root"):
            loader.load_include(StringIO("value: !include ../secret.yaml\n"), source=self.main_source())
        with self.assertRaisesRegex(yaml.YAMLError, "was not found"):
            loader.load_include(StringIO("value: !include absent.yaml\n"), source=self.main_source())
        with self.assertRaisesRegex(yaml.YAMLError, "URI or drive paths"):
            loader.load_include(
                StringIO("value: !include https://example.invalid/base.yaml\n"),
                source=self.main_source(),
            )

    def test_memory_limits_apply_to_injected_streams(self) -> None:
        content = "key: long-value\n"
        with self.assertRaisesRegex(yaml.YAMLError, "per-file size limit"):
            self.loader({"base.yaml": content}, max_file_bytes=4).load_include(
                StringIO("value: !include base.yaml\n"), source=self.main_source()
            )
        with self.assertRaisesRegex(yaml.YAMLError, "maximum include depth"):
            self.loader({"base.yaml": content}, max_include_depth=0).load_include(
                StringIO("value: !include base.yaml\n"), source=self.main_source()
            )
        with self.assertRaisesRegex(yaml.YAMLError, "maximum include count"):
            self.loader({"base.yaml": content}, max_include_files=0).load_include(
                StringIO("value: !include base.yaml\n"), source=self.main_source()
            )
        with self.assertRaisesRegex(yaml.YAMLError, "maximum total included bytes"):
            self.loader({"base.yaml": content}, max_total_include_bytes=3).load_include(
                StringIO("value: !include base.yaml\n"), source=self.main_source()
            )
        with self.assertRaisesRegex(yaml.YAMLError, "maximum total included bytes"):
            self.loader(
                {"first.yaml": "a: 1\n", "second.yaml": "b: 2\n"},
                max_total_include_bytes=8,
            ).load_include(
                StringIO("first: !include first.yaml\nsecond: !include second.yaml\n"),
                source=self.main_source(),
            )
        with self.assertRaisesRegex(yaml.YAMLError, "maximum include depth"):
            load_yaml_include(
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

    def test_injected_stream_is_closed_after_read_and_on_read_error(self) -> None:
        root = self.root
        source = self.main_source()
        resolver = TrackingResolver(root, b"value: [\n")
        with self.assertRaises(yaml.YAMLError):
            load_yaml_include(
                StringIO("value: !include base.yaml\n"),
                source=source,
                allowed_roots=[root],
                include_resolver=resolver,
            )
        self.assertEqual(len(resolver.streams), 1)
        self.assertTrue(resolver.streams[0].closed)

        oversized = TrackingResolver(root, b"value: too large\n")
        with self.assertRaisesRegex(yaml.YAMLError, "per-file size limit"):
            load_yaml_include(
                StringIO("value: !include base.yaml\n"),
                source=source,
                allowed_roots=[root],
                include_resolver=oversized,
                max_file_bytes=2,
            )
        self.assertTrue(oversized.streams[0].closed)

        failing = TrackingResolver(root, b"ignored", fail_read=True)
        with self.assertRaisesRegex(yaml.YAMLError, "injected read failure"):
            load_yaml_include(
                StringIO("value: !include base.yaml\n"),
                source=source,
                allowed_roots=[root],
                include_resolver=failing,
            )
        self.assertTrue(failing.streams[0].closed)

    def test_duplicate_keys_and_python_tags_stay_rejected_with_memory_resolver(self) -> None:
        duplicate = self.loader({"base.yaml": "value: first\nvalue: second\n"})
        with self.assertRaisesRegex(yaml.YAMLError, "duplicate key"):
            duplicate.load_include(StringIO("<<: !include base.yaml\n"), source=self.main_source())

        unsafe = self.loader({"base.yaml": 'value: !!python/object/apply:os.system ["echo unsafe"]\n'})
        with self.assertRaises(yaml.YAMLError):
            unsafe.load_include(StringIO("<<: !include base.yaml\n"), source=self.main_source())

    def test_reused_configured_loader_gets_a_fresh_budget_per_call(self) -> None:
        loader = self.loader({"base.yaml": "value: 1\n"}, max_include_files=1)
        for _ in range(2):
            self.assertEqual(
                loader.load_include(StringIO("value: !include base.yaml\n"), source=self.main_source()),
                {"value": {"value": 1}},
            )

    def test_reused_configured_loader_preserves_default_filesystem_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "base.yaml").write_text("value: 1\n", encoding="utf-8")
            config = root / "main.yaml"
            config.write_text("%LOADER LoadInclude\n---\n<<: !include base.yaml\n", encoding="utf-8")
            loader = YamlConfigLoader(max_include_files=1)
            self.assertEqual(loader.load_file(config), {"value": 1})
            self.assertEqual(loader.load_file(config), {"value": 1})

    def test_memory_source_cannot_be_used_with_an_unconfigured_filesystem_resolver(self) -> None:
        loader = YamlConfigLoader(allowed_roots=[self.root])
        with self.assertRaisesRegex(yaml.YAMLError, "filesystem resolver cannot resolve relative to a memory source"):
            loader.load_include(StringIO("value: !include base.yaml\n"), source=self.main_source())

    def test_custom_resolver_cannot_escape_file_roots_before_opening(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            inside = parent / "inside"
            outside = parent / "outside"
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
            with self.assertRaisesRegex(yaml.YAMLError, "outside the allowed roots"):
                load_yaml_include(
                    StringIO("value: !include base.yaml\n"),
                    source_path=main,
                    allowed_roots=[inside],
                    include_resolver=resolver,
                )
            self.assertFalse(resolver.opened)


if __name__ == "__main__":
    unittest.main()
