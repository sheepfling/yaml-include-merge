"""Load an included file with an application-defined PyYAML tag."""

from pathlib import Path
from pprint import pprint

import yaml

from yaml_compose import load_yaml_file


class UpperLoader(yaml.SafeLoader):
    """SafeLoader extended with the example's trusted ``!upper`` tag."""


def construct_upper(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> str:
    """Return the tagged scalar in uppercase."""
    return loader.construct_scalar(node).upper()


UpperLoader.add_constructor("!upper", construct_upper)


def load_example() -> object:
    """Apply the selected loader with both nested dispatch modes."""
    config = Path(__file__).parent / "custom_loader_config.yaml"
    composed = load_yaml_file(config, yaml_loader=UpperLoader)
    ordinary_child = load_yaml_file(config, yaml_loader=UpperLoader, recursive_dispatch=False)
    assert ordinary_child == composed
    return composed


if __name__ == "__main__":
    result = load_example()
    assert result == {"profile": {"name": "ALICE"}}
    pprint(result)
