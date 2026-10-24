"""Load layered YAML from a caller-supplied in-memory namespace."""

from io import StringIO
import json
from pathlib import PurePosixPath

from yaml_include_merge import (
    MemoryIncludeResolver,
    MemoryRoot,
    MemorySource,
    YamlConfigLoader,
)


def load_example() -> dict[str, object]:
    root = MemoryRoot("demo-config")
    resolver = MemoryIncludeResolver(
        root,
        {
            "configs/shared/base.yaml": "service:\n  host: localhost\n  port: 8000\n  labels: [base]\n",
            "configs/environments/production.yaml": (
                "%LOADER LoadInclude\n---\n"
                "<<: !include ../shared/base.yaml\n"
                "service:\n  port: 8081\n"
            ),
        },
    )
    loader = YamlConfigLoader(
        include_resolver=resolver,
        allowed_roots=[root],
    )
    return loader.load_stream(
        StringIO("%LOADER LoadInclude\n---\n<<: !include environments/production.yaml\n"),
        source=MemorySource(root, PurePosixPath("configs/main.yaml")),
    )


if __name__ == "__main__":
    config = load_example()
    assert config == {"service": {"host": "localhost", "port": 8081, "labels": ["base"]}}
    print(json.dumps(config, indent=2, sort_keys=True))
