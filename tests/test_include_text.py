"""The !include-text constructor uses normal resolution and byte budgets."""

from __future__ import annotations

from io import BytesIO, StringIO
from pathlib import Path, PurePosixPath

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


def test_include_text_preserves_whitespace_line_endings_and_yaml_syntax_as_text(test_path: Path) -> None:
    source = test_path / "literal.yaml"
    contents = "%LOADER YamlCompose\r\n---\r\n!delete  \r\n  indented\t\r\n"
    source.write_bytes(contents.encode("utf-8"))
    config = _config(test_path, "literal: !include-text literal.yaml\n")

    assert load_yaml_file(config) == {"literal": contents}


def test_include_text_decodes_utf8_and_keeps_bom_as_text(test_path: Path) -> None:
    source = test_path / "message.md"
    contents = "\ufeff# café\n"
    source.write_bytes(contents.encode("utf-8"))
    config = _config(test_path, "message: !include-text message.md\n")

    assert load_yaml_file(config) == {"message": contents}


def test_include_text_constructor_is_copied_to_custom_safe_loader(test_path: Path) -> None:
    source = test_path / "template.txt"
    source.write_bytes(b"{{ value }}\n")
    config = _config(test_path, "template: !include-text template.txt\n")

    class CustomSafeLoader(yaml.SafeLoader):
        pass

    assert load_yaml_file(config, yaml_loader=CustomSafeLoader) == {"template": "{{ value }}\n"}


def test_include_text_can_read_its_own_yaml_source_without_recursing(test_path: Path) -> None:
    config = test_path / "main.yaml"
    contents = "%LOADER YamlCompose\n---\npayload: !include-text main.yaml\n"
    config.write_bytes(contents.encode())

    assert load_yaml_file(config) == {"payload": contents}


def test_text_and_raw_can_read_an_ancestor_yaml_source_with_shared_budgets(test_path: Path) -> None:
    parent = test_path / "main.yaml"
    parent_text = "%LOADER YamlCompose\n---\nchild: !include child.yaml\n"
    parent_bytes = parent_text.encode()
    parent.write_bytes(parent_bytes)
    child_text = "text: !include-text main.yaml\nraw: !include-raw main.yaml\n"
    child_bytes = child_text.encode()
    (test_path / "child.yaml").write_bytes(child_bytes)

    assert load_yaml_file(
        parent,
        max_include_depth=2,
        max_include_files=3,
        max_file_bytes=max(len(parent_bytes), len(child_bytes)),
        max_total_include_bytes=len(child_bytes) + 2 * len(parent_bytes),
    ) == {"child": {"text": parent_text, "raw": parent_bytes}}

    with pytest.raises(ConstructorError, match="maximum include count"):
        load_yaml_file(parent, max_include_depth=2, max_include_files=2)


def test_include_text_uses_shared_file_and_byte_budgets(test_path: Path) -> None:
    source = test_path / "message.txt"
    source.write_bytes(b"hello")
    config = _config(test_path, "message: !include-text message.txt\n")

    assert load_yaml_file(config, max_file_bytes=5, max_total_include_bytes=5) == {"message": "hello"}
    with pytest.raises(ConstructorError, match="per-file size limit") as caught:
        load_yaml_file(config, max_file_bytes=4)
    assert str(source) in str(caught.value)

    with pytest.raises(ConstructorError, match=r"maximum include count \(0\) exceeded"):
        load_yaml_file(config, max_include_files=0)

    with pytest.raises(ConstructorError, match=r"maximum include depth \(0\) exceeded"):
        load_yaml_file(config, max_include_depth=0)

    with pytest.raises(ConstructorError, match=r"maximum total included bytes \(4\) exceeded"):
        load_yaml_file(config, max_total_include_bytes=4)


def test_include_text_custom_resolver_remains_root_confined_and_closes_stream(test_path: Path) -> None:
    root = MemoryRoot("text-test")
    stream = BytesIO(b"literal text")

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            del request
            return ResolvedInclude(MemorySource(root, PurePosixPath("notes.txt")), lambda: stream)

    loader = YamlConfigLoader(include_resolver=Resolver(), allowed_roots=[root])
    assert loader.load_stream(
        StringIO("%LOADER YamlCompose\n---\nliteral: !include-text notes.txt\n"),
        source=MemorySource(root, PurePosixPath("main.yaml")),
    ) == {"literal": "literal text"}
    assert stream.closed


def test_include_text_closes_custom_stream_when_utf8_decoding_fails() -> None:
    root = MemoryRoot("text-invalid-utf8")
    stream = BytesIO(b"\xff")

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            del request
            return ResolvedInclude(MemorySource(root, PurePosixPath("binary.txt")), lambda: stream)

    loader = YamlConfigLoader(include_resolver=Resolver(), allowed_roots=[root])
    with pytest.raises(ConstructorError, match="not UTF-8 text"):
        loader.load_stream(
            StringIO("%LOADER YamlCompose\n---\ntext: !include-text binary.txt\n"),
            source=MemorySource(root, PurePosixPath("main.yaml")),
        )
    assert stream.closed


def test_include_text_rejects_resolver_sources_outside_allowed_roots() -> None:
    allowed = MemoryRoot("allowed")
    outside = MemoryRoot("outside")

    class Resolver:
        def resolve(self, request: IncludeRequest) -> ResolvedInclude:
            del request
            return ResolvedInclude(MemorySource(outside, PurePosixPath("notes.txt")), lambda: BytesIO(b"unsafe"))

    loader = YamlConfigLoader(include_resolver=Resolver(), allowed_roots=[allowed])
    with pytest.raises(ConstructorError, match="outside the allowed roots"):
        loader.load_stream(
            StringIO("%LOADER YamlCompose\n---\nliteral: !include-text notes.txt\n"),
            source=MemorySource(allowed, PurePosixPath("main.yaml")),
        )


def test_include_text_rejects_invalid_utf8_with_source_context(test_path: Path) -> None:
    source = test_path / "binary.txt"
    source.write_bytes(b"valid\xff")
    config = _config(test_path, "value: !include-text binary.txt\n")

    with pytest.raises(ConstructorError, match="is not UTF-8 text") as caught:
        load_yaml_file(config)
    assert str(source) in str(caught.value)
    assert "while resolving !include-text" in str(caught.value)
    assert caught.value.problem_mark is not None
    assert isinstance(caught.value.__cause__, UnicodeDecodeError)


def test_include_text_missing_source_is_a_marked_include_error(test_path: Path) -> None:
    config = _config(test_path, "value: !include-text absent.txt\n")

    with pytest.raises(ConstructorError, match=r"absent\.txt") as caught:
        load_yaml_file(config)
    assert "while resolving !include-text" in str(caught.value)
    assert caught.value.problem_mark is not None
