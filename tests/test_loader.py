from __future__ import annotations

import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path

import yaml

from yaml_include_merge import load_yaml_dispatch, load_yaml_file


class LoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_dispatch_loads_ordinary_yaml_safely_and_rejects_duplicates(self) -> None:
        ordinary = self.write("ordinary.yaml", "service:\n  port: 8080\n")
        self.assertEqual(load_yaml_file(ordinary), {"service": {"port": 8080}})

        duplicate = self.write("duplicate.yaml", "name: first\nname: second\n")
        with self.assertRaisesRegex(yaml.YAMLError, "duplicate key"):
            load_yaml_file(duplicate)

    def test_safe_loader_does_not_construct_python_objects(self) -> None:
        source = StringIO('value: !!python/object/apply:os.system ["echo unsafe"]\n')
        with self.assertRaises(yaml.YAMLError):
            load_yaml_dispatch(source)

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
            "%LOADER LoadInclude\n"
            "---\n"
            "<<: !include base.yaml\n"
            "service:\n"
            "  port: 8081\n"
            "  token: !delete\n"
            "  labels: [production]\n"
            "feature: null\n"
            "backend: remote\n",
        )

        result = load_yaml_file(production)

        self.assertEqual(
            result,
            {
                "service": {
                    "host": "127.0.0.1",
                    "port": 8081,
                    "labels": ["production"],
                },
                "feature": None,
                "backend": "remote",
            },
        )

    def test_nested_includes_resolve_from_the_including_file(self) -> None:
        main = self.write(
            "configs/main.yaml",
            "%LOADER LoadInclude\n---\nvalue: !include nested/value.yaml\n",
        )
        self.write("configs/nested/value.yaml", "result: from-configs\n")
        wrong = self.write("wrong/nested/value.yaml", "result: from-cwd\n")

        original_cwd = Path.cwd()
        try:
            os.chdir(wrong.parents[1])
            result = load_yaml_file(main)
        finally:
            os.chdir(original_cwd)

        self.assertEqual(result, {"value": {"result": "from-configs"}})

    def test_include_paths_cannot_escape_the_allowed_root(self) -> None:
        outside = self.write("outside.yaml", "secret: true\n")
        config = self.write(
            "configs/main.yaml",
            "%LOADER LoadInclude\n---\nvalue: !include ../outside.yaml\n",
        )
        self.assertTrue(outside.exists())

        with self.assertRaisesRegex(yaml.YAMLError, "outside the allowed roots"):
            load_yaml_file(config)

    def test_symlink_to_outside_root_is_rejected(self) -> None:
        outside = self.write("outside.yaml", "secret: true\n")
        configs = self.root / "configs"
        configs.mkdir()
        link = configs / "outside.yaml"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks are unavailable: {exc}")
        config = self.write(
            "configs/main.yaml",
            "%LOADER LoadInclude\n---\nvalue: !include outside.yaml\n",
        )

        with self.assertRaisesRegex(yaml.YAMLError, "outside the allowed roots"):
            load_yaml_file(config)

    def test_include_cycle_reports_a_yaml_source_location(self) -> None:
        first = self.write(
            "first.yaml",
            "%LOADER LoadInclude\n---\nvalue: !include second.yaml\n",
        )
        self.write("second.yaml", "value: !include first.yaml\n")

        with self.assertRaisesRegex(yaml.YAMLError, "include cycle detected") as raised:
            load_yaml_file(first)
        self.assertIn("second.yaml", str(raised.exception))
        self.assertIn("line 1", str(raised.exception))

    def test_duplicate_keys_in_an_included_document_are_rejected(self) -> None:
        self.write("base.yaml", "value: first\nvalue: second\n")
        config = self.write(
            "main.yaml",
            "%LOADER LoadInclude\n---\n<<: !include base.yaml\n",
        )
        with self.assertRaisesRegex(yaml.YAMLError, "duplicate key"):
            load_yaml_file(config)

    def test_overlay_requires_a_mapping_include(self) -> None:
        self.write("values.yaml", "- one\n- two\n")
        config = self.write(
            "main.yaml",
            "%LOADER LoadInclude\n---\n<<: !include values.yaml\n",
        )
        with self.assertRaisesRegex(yaml.YAMLError, "included value for << must be a mapping"):
            load_yaml_file(config)

    def test_include_depth_is_bounded(self) -> None:
        self.write("first.yaml", "value: !include second.yaml\n")
        self.write("second.yaml", "value: done\n")
        config = self.write(
            "main.yaml",
            "%LOADER LoadInclude\n---\nvalue: !include first.yaml\n",
        )
        with self.assertRaisesRegex(yaml.YAMLError, "maximum include depth"):
            load_yaml_file(config, max_include_depth=1)

    def test_include_size_and_count_limits_are_enforced(self) -> None:
        self.write("base.yaml", "value: long-enough\n")
        config = self.write(
            "main.yaml",
            "%LOADER LoadInclude\n---\n<<: !include base.yaml\n",
        )
        with self.assertRaisesRegex(yaml.YAMLError, "per-file size limit"):
            load_yaml_file(config, max_file_bytes=4)
        with self.assertRaisesRegex(yaml.YAMLError, "maximum include count"):
            load_yaml_file(config, max_include_files=0)

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
        self.assertEqual(result["service"], {"host": "localhost", "port": 8000})
        self.assertEqual(result["merged-service"], {"host": "localhost", "port": 8100})

    def test_include_overlay_does_not_confuse_anchor_merge_with_file_include(self) -> None:
        config = self.write(
            "anchors.yaml",
            "%LOADER LoadInclude\n"
            "---\n"
            "defaults: &defaults {host: localhost}\n"
            "service:\n"
            "  <<: *defaults\n",
        )
        with self.assertRaisesRegex(yaml.YAMLError, "must have a !include value"):
            load_yaml_file(config)


if __name__ == "__main__":
    unittest.main()
