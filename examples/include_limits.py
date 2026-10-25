"""Load a layered configuration with no include-count limit."""

from pathlib import Path
from pprint import pprint

from yaml_compose import load_yaml_file


def load_example() -> object:
    """Disable the file-count bound for this load while retaining other defaults."""
    return load_yaml_file(Path(__file__).parent / "production.yaml", max_include_files=None)


if __name__ == "__main__":
    pprint(load_example())
