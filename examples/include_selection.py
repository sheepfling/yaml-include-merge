"""Run the object-form include showcase with its checked-in source fixtures."""

from __future__ import annotations

from pathlib import Path
from pprint import pprint

from yaml_compose import load_yaml_file


def load_example() -> object:
    """Load selection examples independently of the caller's working directory."""
    return load_yaml_file(Path(__file__).with_name("include_selection") / "main.yaml")


if __name__ == "__main__":
    # pprint preserves the distinction between bytes, strings, and nested lists.
    pprint(load_example(), sort_dicts=False)
