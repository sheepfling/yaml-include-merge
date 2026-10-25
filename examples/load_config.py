"""Load the runnable YAML examples used in the top-level README."""

import json
from pathlib import Path

from yaml_compose import load_yaml_file


def load_examples() -> dict[str, object]:
    """Return each simple example's composed value for display or verification."""
    examples = Path(__file__).parent
    return {
        "quickstart": load_yaml_file(examples / "quickstart" / "production.yaml"),
        "include_value": load_yaml_file(examples / "include_value.yaml"),
        "shallow_merge": load_yaml_file(examples / "shallow_merge.yaml"),
        "deep_merge": load_yaml_file(examples / "deep_merge.yaml"),
        "merge_precedence": load_yaml_file(examples / "merge_precedence.yaml"),
        "spread_sources": load_yaml_file(examples / "spread_sources.yaml"),
        "replace_mapping": load_yaml_file(examples / "replace_mapping.yaml"),
    }


if __name__ == "__main__":
    print(json.dumps(load_examples(), indent=2, sort_keys=True))
