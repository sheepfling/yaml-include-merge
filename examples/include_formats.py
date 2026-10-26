"""Load YAML that composes JSON and TOML include sources."""

from __future__ import annotations

from pathlib import Path
from pprint import pprint

from yaml_compose import load_yaml_file


def load_example() -> object:
    """Return the exact composition shown in the worked format example."""
    return load_yaml_file(Path(__file__).with_name("include_formats.yaml"))


if __name__ == "__main__":
    pprint(load_example(), sort_dicts=False)
