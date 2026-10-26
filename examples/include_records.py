"""Load and print the documented JSON Lines and NDJSON composition example."""

from __future__ import annotations

from pathlib import Path
from pprint import pprint

from yaml_compose import load_yaml_file


def load_example() -> object:
    """Return the exact record-format composition shown in the worked guide."""
    return load_yaml_file(Path(__file__).with_name("include_records.yaml"))


if __name__ == "__main__":
    pprint(load_example(), sort_dicts=False)
