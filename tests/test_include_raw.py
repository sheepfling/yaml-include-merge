"""The !include-raw constructor returns exact resolver-owned bytes."""

from __future__ import annotations

from io import BytesIO, StringIO
from pathlib import Path, PurePosixPath
from typing import BinaryIO, cast

import pytest
import yaml
from yaml.constructor import ConstructorError

from yaml_compose import (
    IncludeRequest,
    MemoryRoot,
    MemorySource,
    ResolvedInclude,
    YamlConfigLoader,
    load_yaml_file,
)


def _config(path: Path, body: str) -> Path:
    config = path / "main.yaml"
    config.write_text(f"%LOADER YamlCompose\n---\n{body}", encoding="utf-8")
    return config


def test_include_raw_preserves_every_byte_and_empty_sources(test_path: Path) -> None:
    payload = bytes(range(256)) + b"\x00\xff\r\n"
    (test_path / "all-bytes.bin").write_bytes(payload)
    (test_path / "empty.bin").write_bytes(b"")
    config = _config(test_path, "payload: !include-raw all-bytes.bin\nempty: !include-raw empty.bin\n")

    assert load_yaml_file(config) == {"payload": payload, "empty": b""}


def test_include_raw_constructor_is_available_in_custom_safe_loader(test_path: Path) -> None:
    source = test_path / "payload.bin"
    source.write_bytes(b"\x00\xff")
    config = _config(test_path, "payload: !include-raw payload.bin\n")

    class CustomSafeLoader(yaml.SafeLoader):
        pass

    assert load_yaml_file(config, yaml_loader=CustomSafeLoader) == {"payload": b"\x00\xff"}


def test_include_raw_can_read_its_own_yaml_source_without_recursing(test_path: Path) -> None:
    config = test_path / "main.yaml"
    contents = b"%LOADER YamlCompose\n---\npayload: !include-raw main.yaml\n"
    config.write_bytes(contents)

    assert load_yaml_file(config) == {"payload": contents}


def test_include_raw_custom_resolver_preserves_bytes_and_closes_stream() -> None:
    root = MemoryRoot("raw-test")
    payload = b"\x00\xffnot-utf8\x80\r\n"
    stream = BytesIO(payload)

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            return ResolvedInclude(MemorySource(root, PurePosixPath(request.name)), lambda: stream)

    loader = YamlConfigLoader(include_resolver=Resolver(), allowed_roots=[root])
    result = loader.load_stream(
        StringIO("%LOADER YamlCompose\n---\npayload: !include-raw payload.bin\n"),
        source=MemorySource(root, PurePosixPath("main.yaml")),
    )

    assert result == {"payload": payload}
    assert stream.closed


def test_include_raw_closes_custom_stream_when_a_byte_budget_fails() -> None:
    root = MemoryRoot("raw-budget")
    stream = BytesIO(b"\x00\xffpayload")

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            del request
            return ResolvedInclude(MemorySource(root, PurePosixPath("payload.bin")), lambda: stream)

    loader = YamlConfigLoader(
        include_resolver=Resolver(),
        allowed_roots=[root],
        max_file_bytes=3,
    )
    with pytest.raises(ConstructorError, match="per-file size limit"):
        loader.load_stream(
            StringIO("%LOADER YamlCompose\n---\npayload: !include-raw payload.bin\n"),
            source=MemorySource(root, PurePosixPath("main.yaml")),
        )
    assert stream.closed


def test_include_raw_rejects_text_stream_without_encoding_it() -> None:
    root = MemoryRoot("raw-text-stream")
    stream = StringIO("text that must not be encoded")

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            return ResolvedInclude(
                MemorySource(root, PurePosixPath("payload.bin")),
                lambda: cast(BinaryIO, stream),
            )

    loader = YamlConfigLoader(include_resolver=Resolver(), allowed_roots=[root])
    with pytest.raises(ConstructorError, match="include resolver streams must return bytes"):
        loader.load_stream(
            StringIO("%LOADER YamlCompose\n---\npayload: !include-raw payload.bin\n"),
            source=MemorySource(root, PurePosixPath("main.yaml")),
        )
    assert stream.closed


def test_include_raw_uses_shared_depth_count_and_byte_budgets(test_path: Path) -> None:
    payload = b"\x00\xffx"
    (test_path / "payload.bin").write_bytes(payload)
    config = _config(test_path, "payload: !include-raw payload.bin\n")

    assert load_yaml_file(
        config,
        max_file_bytes=len(payload),
        max_total_include_bytes=len(payload),
        max_include_files=1,
        max_include_depth=1,
    ) == {"payload": payload}
    with pytest.raises(ConstructorError, match="per-file size limit"):
        load_yaml_file(config, max_file_bytes=len(payload) - 1)
    with pytest.raises(ConstructorError, match="maximum total included bytes"):
        load_yaml_file(config, max_total_include_bytes=len(payload) - 1)
    with pytest.raises(ConstructorError, match="maximum include count"):
        load_yaml_file(config, max_include_files=0)
    with pytest.raises(ConstructorError, match="maximum include depth"):
        load_yaml_file(config, max_include_depth=0)


def test_include_raw_shares_total_bytes_with_other_include_tags(test_path: Path) -> None:
    (test_path / "payload.bin").write_bytes(b"\x00\xff")
    (test_path / "message.txt").write_bytes(b"x")
    config = _config(
        test_path,
        "payload: !include-raw payload.bin\nmessage: !include-text message.txt\n",
    )

    with pytest.raises(ConstructorError, match="maximum total included bytes"):
        load_yaml_file(config, max_total_include_bytes=2)


def test_include_raw_custom_resolver_remains_root_confined() -> None:
    allowed = MemoryRoot("raw-allowed")
    outside = MemoryRoot("raw-outside")
    opened = False

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            del request

            def open_binary() -> BytesIO:
                nonlocal opened
                opened = True
                return BytesIO(b"blocked")

            return ResolvedInclude(MemorySource(outside, PurePosixPath("payload.bin")), open_binary)

    loader = YamlConfigLoader(include_resolver=Resolver(), allowed_roots=[allowed])
    with pytest.raises(ConstructorError, match="outside the allowed roots"):
        loader.load_stream(
            StringIO("%LOADER YamlCompose\n---\npayload: !include-raw payload.bin\n"),
            source=MemorySource(allowed, PurePosixPath("main.yaml")),
        )
    assert not opened
