"""Executable example for the include and overlay loader."""

from pathlib import Path
import json

from yaml_include_merge import load_yaml_file


if __name__ == "__main__":
    config = load_yaml_file(Path(__file__).with_name("production.yaml"))
    assert config == {
        "service": {
            "host": "api.us.example.test",
            "port": 8081,
            "labels": ["production"],
            "clearable": [],
            "token": None,
            "nested": {"retries": 3, "timeout_seconds": 45},
        },
        "logging": {"level": "warning", "format": "json"},
    }
    print(json.dumps(config, indent=2, sort_keys=True))
