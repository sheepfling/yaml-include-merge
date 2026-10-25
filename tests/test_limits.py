from __future__ import annotations

import inspect
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from io import StringIO
from pathlib import Path, PurePosixPath

import yaml

from yaml_compose import (
    COMPOSE_LOADER_TAG,
    DEFAULT_INCLUDE_LIMITS,
    UNSET,
    IncludeLimits,
    MemoryIncludeResolver,
    MemoryRoot,
    MemorySource,
    YamlConfigLoader,
    load_yaml_compose,
    load_yaml_dispatch,
    load_yaml_file,
)


class IncludeLimitsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        (self.root / "base.yaml").write_text("value: 1\n", encoding="utf-8")
        self.config = self.root / "main.yaml"
        self.config.write_text(
            "%LOADER YamlCompose\n---\n<<<: !include base.yaml\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_shared_defaults_are_inspectable_and_used_by_every_entry_point(self) -> None:
        self.assertEqual(
            DEFAULT_INCLUDE_LIMITS,
            IncludeLimits(
                max_include_depth=16,
                max_include_files=64,
                max_file_bytes=1_048_576,
                max_total_include_bytes=8_388_608,
            ),
        )
        with self.assertRaises(FrozenInstanceError):
            DEFAULT_INCLUDE_LIMITS.max_include_files = 1  # type: ignore[misc]

        for entry_point in (load_yaml_dispatch, load_yaml_compose, load_yaml_file):
            self.assertIs(
                inspect.signature(entry_point).parameters["include_limits"].default,
                UNSET,
            )
        self.assertIs(
            inspect.signature(YamlConfigLoader).parameters["include_limits"].default,
            UNSET,
        )
        self.assertIs(YamlConfigLoader().include_limits, DEFAULT_INCLUDE_LIMITS)
        self.assertEqual(load_yaml_file(self.config), {"value": 1})
        self.assertEqual(YamlConfigLoader().load_file(self.config), {"value": 1})
        self.assertEqual(
            load_yaml_dispatch(
                StringIO("value: !include base.yaml\n"),
                loader_tag=COMPOSE_LOADER_TAG,
                source_path=self.config,
            ),
            {"value": {"value": 1}},
        )
        self.assertEqual(
            load_yaml_compose(
                StringIO("value: !include base.yaml\n"),
                source_path=self.config,
            ),
            {"value": {"value": 1}},
        )

    def test_profiles_apply_across_entry_points_and_legacy_keywords_override(self) -> None:
        no_includes = IncludeLimits(max_include_files=0)
        stream = StringIO("value: !include base.yaml\n")
        with self.assertRaisesRegex(yaml.YAMLError, "maximum include count"):
            load_yaml_file(self.config, include_limits=no_includes)
        with self.assertRaisesRegex(yaml.YAMLError, "maximum include count"):
            load_yaml_dispatch(
                stream,
                loader_tag=COMPOSE_LOADER_TAG,
                source_path=self.config,
                include_limits=no_includes,
            )
        with self.assertRaisesRegex(yaml.YAMLError, "maximum include count"):
            load_yaml_compose(
                StringIO("value: !include base.yaml\n"),
                source_path=self.config,
                include_limits=no_includes,
            )
        with self.assertRaisesRegex(yaml.YAMLError, "maximum include count"):
            YamlConfigLoader(include_limits=no_includes).load_file(self.config)

        self.assertEqual(
            load_yaml_file(self.config, include_limits=no_includes, max_include_files=1),
            {"value": 1},
        )
        self.assertEqual(
            load_yaml_dispatch(
                StringIO("value: !include base.yaml\n"),
                loader_tag=COMPOSE_LOADER_TAG,
                source_path=self.config,
                include_limits=no_includes,
                max_include_files=1,
            ),
            {"value": {"value": 1}},
        )
        self.assertEqual(
            load_yaml_compose(
                StringIO("value: !include base.yaml\n"),
                source_path=self.config,
                include_limits=no_includes,
                max_include_files=1,
            ),
            {"value": {"value": 1}},
        )
        configured = YamlConfigLoader(
            include_limits=no_includes,
            max_include_files=1,
        )
        self.assertEqual(configured.include_limits.max_include_files, 1)
        configured.max_include_files = 0
        self.assertEqual(configured.include_limits.max_include_files, 0)
        configured.max_include_files = 1
        self.assertEqual(configured.load_file(self.config), {"value": 1})

    def test_zero_limits_are_valid_and_invalid_values_are_rejected(self) -> None:
        self.assertEqual(IncludeLimits(max_include_depth=0).max_include_depth, 0)
        with self.assertRaisesRegex(ValueError, "non-negative integers"):
            IncludeLimits(max_include_depth=-1)
        with self.assertRaisesRegex(ValueError, "non-negative integers"):
            IncludeLimits(max_file_bytes="large")  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "IncludeLimits instance"):
            YamlConfigLoader(include_limits=object())  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "non-negative integers"):
            load_yaml_file(self.config, max_include_files=-1)
        with self.assertRaisesRegex(yaml.YAMLError, r"maximum include depth \(0\)"):
            load_yaml_file(self.config, include_limits=IncludeLimits(max_include_depth=0))

    def test_shared_profile_has_no_per_load_counter_state(self) -> None:
        root = MemoryRoot("shared-limit-test")
        limits = IncludeLimits(max_include_files=1)
        files = {"base.yaml": "value: 1\n"}
        loaders = [
            YamlConfigLoader(
                include_resolver=MemoryIncludeResolver(root, files),
                allowed_roots=[root],
                include_limits=limits,
            )
            for _ in range(2)
        ]
        source = MemorySource(root, PurePosixPath("main.yaml"))
        for loader in loaders:
            for _ in range(2):
                self.assertEqual(
                    loader.load_include(StringIO("value: !include base.yaml\n"), source=source),
                    {"value": {"value": 1}},
                )
            self.assertIs(loader.include_limits, limits)
        self.assertEqual(limits.max_include_files, 1)


if __name__ == "__main__":
    unittest.main()
