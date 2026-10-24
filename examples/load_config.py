"""Executable example for the include and overlay loader."""

from pathlib import Path
import json

from yaml_include_merge import load_yaml_file


if __name__ == "__main__":
    config = load_yaml_file(Path(__file__).with_name("production.yaml"))
    print(json.dumps(config, indent=2, sort_keys=True))
