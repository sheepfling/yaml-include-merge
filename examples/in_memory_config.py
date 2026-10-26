"""Load layered YAML from a caller-supplied in-memory namespace."""

import json
from collections.abc import Hashable
from io import StringIO
from pathlib import PurePosixPath

from yaml_compose import (
    MemoryIncludeResolver,
    MemoryRoot,
    MemorySource,
    YamlConfigLoader,
)


# opaque-boundary: The example returns dynamically parsed YAML values.
def load_example() -> dict[Hashable, object]:
    """Resolve a layered YAML document entirely from an in-memory namespace."""
    root = MemoryRoot("demo-config")
    resolver = MemoryIncludeResolver(
        root,
        {
            "configs/shared/base.yaml": ("service:\n  host: localhost\n  port: 8000\n  labels: [base]\n"),
            "configs/environments/production.yaml": (
                "%LOADER YamlCompose\n---\n<<<: !include ../shared/base.yaml\nservice:\n  port: 8081\n"
            ),
        },
    )
    loader = YamlConfigLoader(
        include_resolver=resolver,
        allowed_roots=[root],
    )
    result = loader.load_stream(
        StringIO("%LOADER YamlCompose\n---\n<<<: !include environments/production.yaml\n"),
        source=MemorySource(root, PurePosixPath("configs/main.yaml")),
    )
    if not isinstance(result, dict):
        raise TypeError("expected an in-memory configuration mapping")
    return result


if __name__ == "__main__":
    config = load_example()
    assert config == {"service": {"host": "localhost", "port": 8081, "labels": ["base"]}}
    print(json.dumps(config, indent=2, sort_keys=True))
