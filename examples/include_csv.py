"""Show a CSV table include that keeps each row as strings."""

from pathlib import Path

from yaml_compose import YamlConfigLoader


def load_example() -> object:
    """Load a CSV table through the default filesystem resolver."""
    root = Path(__file__).with_name("include_csv")
    config = root / "main.yaml"
    return YamlConfigLoader().load_file(config)
