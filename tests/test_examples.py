from __future__ import annotations

from pathlib import Path
import unittest

from yaml_include_merge import load_yaml_file
from examples.in_memory_config import load_example


class ExampleTests(unittest.TestCase):
    def test_filesystem_example_resolves_all_layers_and_overrides(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        result = load_yaml_file(repo_root / "examples" / "production.yaml")
        self.assertEqual(
            result,
            {
                "service": {
                    "host": "api.us.example.test",
                    "port": 8081,
                    "labels": ["production"],
                    "clearable": [],
                    "token": None,
                    "nested": {"retries": 3, "timeout_seconds": 45},
                },
                "logging": {"level": "warning", "format": "json"},
            },
        )

    def test_in_memory_example_resolves_nested_relative_include(self) -> None:
        self.assertEqual(
            load_example(),
            {"service": {"host": "localhost", "port": 8081, "labels": ["base"]}},
        )


if __name__ == "__main__":
    unittest.main()
