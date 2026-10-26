from __future__ import annotations

import os
from io import StringIO
from pathlib import Path

import pytest
import yaml

from yaml_compose import (
    DEFAULT_INCLUDE_LIMITS,
    IncludeLimits,
    YamlConfigLoader,
    YamlIncludeError,
    get_default_include_limits,
    load_yaml_compose,
    load_yaml_dispatch,
    load_yaml_file,
    set_default_include_limits,
)


class TestLoader:
    root: Path

    @pytest.fixture(autouse=True)
    def _root(self, test_path: Path) -> None:
        self.root = test_path

    def write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_dispatch_loads_ordinary_yaml_safely_and_rejects_duplicates(self) -> None:
        ordinary = self.write("ordinary.yaml", "service:\n  port: 8080\n")
        assert load_yaml_file(ordinary) == {"service": {"port": 8080}}

        duplicate = self.write("duplicate.yaml", "name: first\nname: second\n")
        with pytest.raises(yaml.YAMLError, match="duplicate key"):
            load_yaml_file(duplicate)

    @pytest.mark.parametrize("entry", ["dispatch", "compose", "facade-stream", "facade-include"])
    @pytest.mark.parametrize("document", ["value: 1\n", "%LOADER YamlCompose\n---\nvalue: !include base.yaml\n"])
    def test_file_descriptor_streams_do_not_require_a_path_name(self, entry: str, document: str) -> None:
        self.write("base.yaml", "1\n")
        config = self.write("stream.yaml", document)
        loader = YamlConfigLoader(search_paths=[self.root])
        with os.fdopen(os.open(config, os.O_RDONLY), "r", encoding="utf-8") as stream:
            if entry == "dispatch":
                result = load_yaml_dispatch(stream, search_paths=[self.root])
            elif entry == "compose":
                result = load_yaml_compose(stream, search_paths=[self.root])
            elif entry == "facade-stream":
                result = loader.load_stream(stream)
            else:
                result = loader.load_include(stream)
        assert result == {"value": 1}

    def test_pyyaml_scalar_resolution_is_retained(self) -> None:
        text = "enabled: on\nanswer: yes\nlegacy_number: 010\n"
        expected = {"enabled": True, "answer": True, "legacy_number": 8}
        assert load_yaml_dispatch(StringIO(text)) == expected
        assert load_yaml_compose(StringIO(text), search_paths=[self.root]) == expected

    def test_python_equal_mapping_keys_are_rejected(self) -> None:
        with pytest.raises(yaml.YAMLError, match="duplicate key"):
            load_yaml_dispatch(StringIO("true: yes\n1: one\n"))

    def test_safe_loader_does_not_construct_python_objects(self) -> None:
        source = StringIO('value: !!python/object/apply:os.system ["echo unsafe"]\n')
        with pytest.raises(yaml.YAMLError):
            load_yaml_dispatch(source)

    def test_custom_safe_loader_is_used_for_ordinary_and_included_files(self) -> None:
        class UpperLoader(yaml.SafeLoader):
            pass

        def upper(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> str:
            return loader.construct_scalar(node).upper()

        UpperLoader.add_constructor("!upper", upper)
        ordinary = self.write("ordinary.yaml", "name: !upper alice\n")
        self.write("nested.yaml", "name: !upper bob\n")
        self.write("base.yaml", "%LOADER YamlCompose\n---\nchild: !include nested.yaml\n")
        config = self.write("config.yaml", "%LOADER YamlCompose\n---\nbase: !include base.yaml\n")

        with pytest.raises(yaml.YAMLError):
            load_yaml_file(ordinary)
        assert load_yaml_file(ordinary, yaml_loader=UpperLoader) == {"name": "ALICE"}
        assert load_yaml_dispatch(StringIO("name: !upper alice\n"), yaml_loader=UpperLoader) == {"name": "ALICE"}
        assert load_yaml_compose(
            StringIO("name: !upper alice\n"), search_paths=[self.root], yaml_loader=UpperLoader
        ) == {"name": "ALICE"}
        expected = {"base": {"child": {"name": "BOB"}}}
        assert load_yaml_file(config, yaml_loader=UpperLoader) == expected
        assert YamlConfigLoader(yaml_loader=UpperLoader).load_file(config) == expected
        with pytest.raises(yaml.YAMLError, match="!include"):
            load_yaml_file(config, yaml_loader=UpperLoader, recursive_dispatch=False)
        with pytest.raises(yaml.YAMLError):
            load_yaml_file(ordinary)
        flat = self.write("flat.yaml", "%LOADER YamlCompose\n---\nchild: !include nested.yaml\n")
        assert load_yaml_file(flat, yaml_loader=UpperLoader, recursive_dispatch=False) == {"child": {"name": "BOB"}}
        self.write("headed.yaml", "%LOADER YamlCompose\n---\nname: !upper bob\n")
        headed = self.write("headed-main.yaml", "%LOADER YamlCompose\n---\nchild: !include headed.yaml\n")
        assert load_yaml_file(headed, yaml_loader=UpperLoader, recursive_dispatch=False) == {"child": {"name": "BOB"}}

        duplicate = self.write("duplicate-custom.yaml", "name: one\nname: two\n")
        with pytest.raises(yaml.YAMLError, match="duplicate key"):
            load_yaml_file(duplicate, yaml_loader=UpperLoader)

    @pytest.mark.parametrize("loader_class", [yaml.FullLoader, yaml.Loader, yaml.UnsafeLoader])
    def test_other_pyyaml_loaders_are_supported(self, loader_class: type[object]) -> None:
        ordinary = self.write("ordinary.yaml", "name: alice\n")
        self.write(
            "base.yaml",
            "service:\n"
            "  host: localhost\n"
            "  labels: [base]\n"
            "  options: {enabled: true, retries: 3}\n"
            "obsolete: remove-me\n",
        )
        self.write("labels.yaml", "- base\n")
        config = self.write(
            "config.yaml",
            "%LOADER YamlCompose\n---\n"
            "<<<: !include base.yaml\n"
            "service:\n"
            "  labels: [!spread [!include labels.yaml], production]\n"
            "  options: !replace {retries: 1}\n"
            "obsolete: !delete\n",
        )
        duplicate = self.write("duplicate.yaml", "name: one\nname: two\n")

        assert load_yaml_file(ordinary, yaml_loader=loader_class) == {"name": "alice"}
        expected = {
            "service": {
                "host": "localhost",
                "labels": ["base", "production"],
                "options": {"retries": 1},
            }
        }
        assert load_yaml_file(config, yaml_loader=loader_class) == expected
        assert YamlConfigLoader(yaml_loader=loader_class).load_file(config) == expected
        with pytest.raises(yaml.YAMLError, match="duplicate key"):
            load_yaml_file(duplicate, yaml_loader=loader_class)

    def test_non_pyyaml_loader_classes_are_rejected(self) -> None:
        ordinary = self.write("ordinary.yaml", "name: alice\n")
        for loader_class in (object, str):
            with pytest.raises(TypeError, match="PyYAML loader"):
                load_yaml_file(ordinary, yaml_loader=loader_class)
            with pytest.raises(TypeError, match="PyYAML loader"):
                YamlConfigLoader(yaml_loader=loader_class)

    def test_custom_full_loader_preserves_application_and_package_tags(self) -> None:
        class AppLoader(yaml.FullLoader):
            pass

        def upper(loader: yaml.FullLoader, node: yaml.ScalarNode) -> str:
            return loader.construct_scalar(node).upper()

        AppLoader.add_constructor("!upper", upper)
        self.write("base.yaml", "name: !upper alice\n")
        config = self.write("config.yaml", "%LOADER YamlCompose\n---\n<<<: !include base.yaml\n")

        assert load_yaml_file(config, yaml_loader=AppLoader) == {"name": "ALICE"}

    def test_file_loader_accepts_pathlike_objects(self) -> None:
        ordinary = self.write("ordinary.yaml", "name: alice\n")

        class ConfigPath(os.PathLike[str]):
            def __init__(self, path: Path) -> None:
                self.path = path

            def __fspath__(self) -> str:
                return str(self.path)

        assert load_yaml_file(ConfigPath(ordinary)) == {"name": "alice"}
        assert YamlConfigLoader().load_file(ConfigPath(ordinary)) == {"name": "alice"}

    def test_pathlike_stream_sources_and_roots_are_supported(self) -> None:
        class ConfigPath(os.PathLike[str]):
            def __init__(self, path: Path) -> None:
                self.path = path

            def __fspath__(self) -> str:
                return str(self.path)

        self.write("base.yaml", "name: included\n")
        main = self.write("main.yaml", "")
        stream = StringIO("%LOADER YamlCompose\n---\nvalue: !include base.yaml\n")

        result = load_yaml_dispatch(
            stream,
            source_path=ConfigPath(main),
            allowed_roots=[ConfigPath(self.root)],
        )
        assert result == {"value": {"name": "included"}}

        result = load_yaml_compose(
            StringIO("value: !include base.yaml\n"),
            search_paths=[ConfigPath(self.root)],
        )
        assert result == {"value": {"name": "included"}}

    def test_include_overlay_deep_merges_maps_and_replaces_other_values(self) -> None:
        self.write(
            "base.yaml",
            "service:\n"
            "  host: 127.0.0.1\n"
            "  port: 8080\n"
            "  token: secret\n"
            "  labels: [base]\n"
            "feature: true\n"
            "backend:\n"
            "  mode: pooled\n",
        )
        production = self.write(
            "production.yaml",
            "%LOADER YamlCompose\n"
            "---\n"
            "<<<: !include base.yaml\n"
            "service:\n"
            "  port: 8081\n"
            "  token: !delete\n"
            "  labels: [production]\n"
            "feature: null\n"
            "backend: remote\n",
        )

        result = load_yaml_file(production)

        assert result == {
            "service": {"host": "127.0.0.1", "port": 8081, "labels": ["production"]},
            "feature": None,
            "backend": "remote",
        }

    @pytest.mark.parametrize("contents", ["value: 1\n", "name: example\n"])
    def test_spread_file_requires_a_sequence(self, contents: str) -> None:
        self.write("items.yaml", contents)
        config = self.write("config.yaml", "%LOADER YamlCompose\n---\nitems:\n  - !spread [!include items.yaml]\n")
        with pytest.raises(yaml.YAMLError, match="!spread source must be a sequence"):
            load_yaml_file(config)

    @pytest.mark.parametrize(
        ("document", "message"),
        [
            ("items: [!spread [*missing]]", "undefined alias"),
            ("items: [!spread [*later]]\nlater: &later [x]", "undefined alias"),
            ("name: &name value\nitems: [!spread [*name]]", "source must be a sequence"),
        ],
    )
    def test_spread_anchor_requires_an_earlier_sequence(self, document: str, message: str) -> None:
        config = self.write("config.yaml", f"%LOADER YamlCompose\n---\n{document}\n")
        with pytest.raises(yaml.YAMLError, match=message):
            load_yaml_file(config)

    def test_spread_tags_are_only_valid_as_sequence_items(self) -> None:
        self.write("items.yaml", "- remote\n")
        config = self.write("config.yaml", "%LOADER YamlCompose\n---\nitems: !spread [!include items.yaml]\n")
        with pytest.raises(YamlIncludeError, match="only valid as a list item"):
            load_yaml_file(config)

    def test_bare_spread_tag_is_not_supported(self) -> None:
        config = self.write("config.yaml", '%LOADER YamlCompose\n---\nitems: [!spread ""]\n')
        with pytest.raises(yaml.YAMLError, match="expects exactly one sequence source"):
            load_yaml_file(config)

    def test_spread_requires_one_source(self) -> None:
        for source in ("[]", "[[], []]"):
            config = self.write("config.yaml", f"%LOADER YamlCompose\n---\nitems: [!spread {source}]\n")
            with pytest.raises(yaml.YAMLError, match="expects exactly one sequence source"):
                load_yaml_file(config)

    @pytest.mark.parametrize(
        ("document", "message"),
        [
            ("value: !replace {port: 1}", "only valid in a deep mapping overlay"),
            (
                "base: &base {value: {host: localhost}}\nconfig: {<<: *base, value: !replace {port: 1}}",
                "only valid in a deep mapping overlay",
            ),
            (
                "base: &base {value: {host: localhost}}\nconfig: {<<<: *base, value: !replace 1}",
                "expects a mapping or one mapping source",
            ),
            (
                "base: &base {value: {host: localhost}}\nconfig: {<<<: *base, value: !replace [[one, two]]}",
                "source must be a mapping",
            ),
            (
                "base: &base {value: {host: localhost}}\nconfig: {<<<: *base, value: !replace []}",
                "expects a mapping or one mapping source",
            ),
            (
                "base: &base {value: {host: localhost}}\nconfig: {<<<: *base, value: !replace [{one: 1}, {two: 2}]}",
                "expects a mapping or one mapping source",
            ),
            (
                "base: &base {value: {host: localhost}}\nconfig:\n  <<<: *base\n  value: !replace\n    host: !delete",
                "!delete is only valid inside a mapping overlay",
            ),
        ],
    )
    def test_replace_rejects_invalid_uses(self, document: str, message: str) -> None:
        config = self.write("config.yaml", f"%LOADER YamlCompose\n---\n{document}\n")
        with pytest.raises(yaml.YAMLError, match=message):
            load_yaml_file(config)

    def test_replace_tag_requires_composition_mode(self) -> None:
        ordinary = self.write("ordinary.yaml", "value: !replace {port: 8081}\n")
        with pytest.raises(yaml.YAMLError, match="!replace"):
            load_yaml_file(ordinary)

    def test_replace_file_source_must_be_a_mapping(self) -> None:
        self.write("replacement.yaml", "- one\n- two\n")
        config = self.write(
            "config.yaml",
            "%LOADER YamlCompose\n---\n"
            "base: &base {value: {old: true}}\n"
            "config: {<<<: *base, value: !replace [!include replacement.yaml]}\n",
        )
        with pytest.raises(yaml.YAMLError, match="!replace source must be a mapping"):
            load_yaml_file(config)

    def test_replace_only_replaces_its_nested_mapping(self) -> None:
        config = self.write(
            "config.yaml",
            "%LOADER YamlCompose\n---\n"
            "base: &base\n"
            "  service:\n"
            "    host: localhost\n"
            "    nested: {left: 1, right: 2}\n"
            "config:\n"
            "  <<<: *base\n"
            "  service:\n"
            "    nested: !replace {right: 3}\n",
        )
        result = load_yaml_file(config)
        assert isinstance(result, dict)
        assert result["config"] == {"service": {"host": "localhost", "nested": {"right": 3}}}
        assert result["base"] == {"service": {"host": "localhost", "nested": {"left": 1, "right": 2}}}

    def test_spread_and_replace_files_share_the_include_budget(self) -> None:
        self.write("base.yaml", "settings: {old: true}\n")
        self.write("replacement.yaml", "new: true\n")
        self.write("items.yaml", "- first\n- second\n")
        config = self.write(
            "config.yaml",
            "%LOADER YamlCompose\n---\n"
            "<<<: !include base.yaml\n"
            "settings: !replace [!include replacement.yaml]\n"
            "items: [!spread [!include items.yaml]]\n",
        )
        with pytest.raises(yaml.YAMLError, match="maximum include count"):
            load_yaml_file(config, max_include_files=2)
        assert load_yaml_file(config, max_include_files=3) == {
            "settings": {"new": True},
            "items": ["first", "second"],
        }

    @pytest.mark.parametrize(
        "document",
        [
            "items: [!replace {port: 1}]",
            "? !replace {port: 1}\n: value",
        ],
    )
    def test_replace_cannot_be_a_list_item_or_mapping_key(self, document: str) -> None:
        config = self.write("config.yaml", f"%LOADER YamlCompose\n---\n{document}\n")
        with pytest.raises(yaml.YAMLError, match="!replace"):
            load_yaml_file(config)

    def test_old_extend_tag_is_not_supported(self) -> None:
        self.write("base.yaml", "labels: [base]\n")
        config = self.write(
            "production.yaml",
            "%LOADER YamlCompose\n---\n<<<: !include base.yaml\nlabels: !extend [new]\n",
        )
        with pytest.raises(yaml.YAMLError, match="!extend"):
            load_yaml_file(config)

    def test_nested_includes_resolve_from_the_including_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        main = self.write(
            "configs/main.yaml",
            "%LOADER YamlCompose\n---\nvalue: !include nested/value.yaml\n",
        )
        self.write("configs/nested/value.yaml", "result: from-configs\n")
        wrong = self.write("wrong/nested/value.yaml", "result: from-cwd\n")

        monkeypatch.chdir(wrong.parents[1])
        result = load_yaml_file(main)

        assert result == {"value": {"result": "from-configs"}}

    def test_include_paths_cannot_escape_the_allowed_root(self) -> None:
        outside = self.write("outside.yaml", "secret: true\n")
        config = self.write(
            "configs/main.yaml",
            "%LOADER YamlCompose\n---\nvalue: !include ../outside.yaml\n",
        )
        assert outside.exists()

        with pytest.raises(yaml.YAMLError, match="outside the allowed roots"):
            load_yaml_file(config)

    def test_absolute_include_paths_still_obey_allowed_roots(self) -> None:
        inside = self.write("configs/inside.yaml", "value: inside\n")
        shared = self.write("shared/value.yaml", "value: shared\n")
        outside = self.write("outside.yaml", "value: outside\n")
        config = self.write(
            "configs/main.yaml",
            f"%LOADER YamlCompose\n---\nvalue: !include {inside.as_posix()}\n",
        )
        assert load_yaml_file(config) == {"value": {"value": "inside"}}

        config.write_text(
            f"%LOADER YamlCompose\n---\nvalue: !include {shared.as_posix()}\n",
            encoding="utf-8",
        )
        assert load_yaml_file(config, allowed_roots=[shared.parent]) == {"value": {"value": "shared"}}

        config.write_text(
            f"%LOADER YamlCompose\n---\nvalue: !include {outside.as_posix()}\n",
            encoding="utf-8",
        )
        with pytest.raises(yaml.YAMLError, match="outside the allowed roots"):
            load_yaml_file(config)

    def test_explicit_none_loader_tag_disables_header_dispatch(self) -> None:
        self.write("base.yaml", "value: included\n")
        config = self.write(
            "main.yaml",
            "%LOADER YamlCompose\n---\nvalue: !include base.yaml\n",
        )
        assert load_yaml_file(config) == {"value": {"value": "included"}}
        with pytest.raises(yaml.YAMLError):
            load_yaml_file(config, loader_tag=None)

    def test_unknown_loader_selection_is_rejected(self) -> None:
        unknown_header = self.write("unknown.yaml", "%LOADER YamlComopse\n---\nvalue: 1\n")
        with pytest.raises(YamlIncludeError, match="unsupported %LOADER value"):
            load_yaml_file(unknown_header)
        with pytest.raises(YamlIncludeError, match="unsupported loader_tag"):
            load_yaml_dispatch(StringIO("value: 1\n"), loader_tag="YamlComopse")

    @pytest.mark.parametrize("explicit_value", ["null", "''", '""', "[]", "{}"])
    def test_delete_tag_must_be_valueless(self, explicit_value: str) -> None:
        self.write("base.yaml", "token: secret\n")
        config = self.write(
            "main.yaml",
            f"%LOADER YamlCompose\n---\n<<<: !include base.yaml\ntoken: !delete {explicit_value}\n",
        )
        with pytest.raises(yaml.YAMLError, match="!delete does not accept a value"):
            load_yaml_file(config)

    def test_symlink_to_outside_root_is_rejected(self) -> None:
        outside = self.write("outside.yaml", "secret: true\n")
        configs = self.root / "configs"
        configs.mkdir()
        link = configs / "outside.yaml"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlinks are unavailable: {exc}")
        config = self.write(
            "configs/main.yaml",
            "%LOADER YamlCompose\n---\nvalue: !include outside.yaml\n",
        )

        with pytest.raises(yaml.YAMLError, match="outside the allowed roots"):
            load_yaml_file(config)

    def test_include_cycle_reports_a_yaml_source_location(self) -> None:
        first = self.write(
            "first.yaml",
            "%LOADER YamlCompose\n---\nvalue: !include second.yaml\n",
        )
        self.write("second.yaml", "value: !include first.yaml\n")

        with pytest.raises(yaml.YAMLError, match="include cycle detected") as raised:
            load_yaml_file(first)
        assert "second.yaml" in str(raised.value)
        assert "line 1" in str(raised.value)

    def test_duplicate_keys_in_an_included_document_are_rejected(self) -> None:
        self.write("base.yaml", "value: first\nvalue: second\n")
        config = self.write(
            "main.yaml",
            "%LOADER YamlCompose\n---\n<<<: !include base.yaml\n",
        )
        with pytest.raises(yaml.YAMLError, match="duplicate key"):
            load_yaml_file(config)

    def test_overlay_requires_a_mapping_include(self) -> None:
        self.write("values.yaml", "- one\n- two\n")
        config = self.write(
            "main.yaml",
            "%LOADER YamlCompose\n---\n<<<: !include values.yaml\n",
        )
        with pytest.raises(yaml.YAMLError, match="source for <<< must be a mapping"):
            load_yaml_file(config)

    def test_include_depth_is_bounded(self) -> None:
        self.write("first.yaml", "value: !include second.yaml\n")
        self.write("second.yaml", "value: done\n")
        config = self.write(
            "main.yaml",
            "%LOADER YamlCompose\n---\nvalue: !include first.yaml\n",
        )
        with pytest.raises(yaml.YAMLError, match="maximum include depth"):
            load_yaml_file(config, max_include_depth=1)

    def test_include_size_and_count_limits_are_enforced(self) -> None:
        self.write("base.yaml", "value: long-enough\n")
        config = self.write(
            "main.yaml",
            "%LOADER YamlCompose\n---\n<<<: !include base.yaml\n",
        )
        with pytest.raises(yaml.YAMLError, match="per-file size limit"):
            load_yaml_file(config, max_file_bytes=4)
        with pytest.raises(yaml.YAMLError, match="maximum include count"):
            load_yaml_file(config, max_include_files=0)

    def test_library_include_limits_are_inspectable_and_configurable(self) -> None:
        assert DEFAULT_INCLUDE_LIMITS.max_include_depth is not None
        assert DEFAULT_INCLUDE_LIMITS.max_include_depth > 0
        assert DEFAULT_INCLUDE_LIMITS.max_include_files is not None
        assert DEFAULT_INCLUDE_LIMITS.max_include_files > 0
        assert DEFAULT_INCLUDE_LIMITS.max_file_bytes is not None
        assert DEFAULT_INCLUDE_LIMITS.max_file_bytes > 0
        assert DEFAULT_INCLUDE_LIMITS.max_total_include_bytes is not None
        assert DEFAULT_INCLUDE_LIMITS.max_total_include_bytes > 0

        self.write("base.yaml", "value: loaded\n")
        config = self.write("main.yaml", "%LOADER YamlCompose\n---\n<<<: !include base.yaml\n")
        existing_loader = YamlConfigLoader()
        original = get_default_include_limits()
        try:
            configured = IncludeLimits(max_include_files=0)
            set_default_include_limits(configured)
            assert get_default_include_limits() == configured
            assert YamlConfigLoader().max_include_files == 0
            with pytest.raises(yaml.YAMLError, match="maximum include count"):
                load_yaml_file(config)
            assert load_yaml_file(config, max_include_files=1) == {"value": "loaded"}
            assert load_yaml_file(config, max_include_files=None) == {"value": "loaded"}
            set_default_include_limits(IncludeLimits(max_include_files=None))
            assert get_default_include_limits().max_include_files is None
            assert load_yaml_file(config) == {"value": "loaded"}
            assert existing_loader.load_file(config) == {"value": "loaded"}
        finally:
            set_default_include_limits(original)

        with pytest.raises(ValueError):
            IncludeLimits(max_file_bytes=-1)

    def test_anchor_aliases_and_legacy_merge_keys_stay_document_local(self) -> None:
        config = self.write(
            "anchors.yaml",
            "defaults: &defaults\n"
            "  host: localhost\n"
            "  port: 8000\n"
            "service: *defaults\n"
            "merged-service:\n"
            "  <<: *defaults\n"
            "  port: 8100\n",
        )
        result = load_yaml_file(config)
        assert isinstance(result, dict)
        assert result["service"] == {"host": "localhost", "port": 8000}
        assert result["merged-service"] == {"host": "localhost", "port": 8100}

    def test_shallow_anchor_merge_works_in_composition_documents(self) -> None:
        config = self.write(
            "anchors.yaml",
            "%LOADER YamlCompose\n---\ndefaults: &defaults {host: localhost}\nservice:\n  <<: *defaults\n",
        )
        assert load_yaml_file(config) == {
            "defaults": {"host": "localhost"},
            "service": {"host": "localhost"},
        }

    def test_merge_keys_may_not_be_combined_in_one_mapping(self) -> None:
        config = self.write(
            "anchors.yaml",
            "%LOADER YamlCompose\n---\nbase: &base {host: localhost}\nservice: {<<: *base, <<<: *base}\n",
        )
        with pytest.raises(yaml.YAMLError, match="only one << or <<< key"):
            load_yaml_file(config)

    def test_quoted_deep_merge_key_is_a_literal_mapping_key(self) -> None:
        config = self.write(
            "config.yaml",
            '%LOADER YamlCompose\n---\n"<<<": value\n',
        )
        assert load_yaml_file(config) == {"<<<": "value"}

        ordinary = self.write("ordinary.yaml", "<<<: value\n")
        assert load_yaml_file(ordinary) == {"<<<": "value"}
