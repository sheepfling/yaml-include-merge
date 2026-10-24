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


if __name__ == "__main__":
    root = MemoryRoot("demo-config")
    resolver = MemoryIncludeResolver(
        root,
        {
            "base.yaml": "service:\n  host: localhost\n  port: 8000\n",
        },
    )
    loader = YamlConfigLoader(
        include_resolver=resolver,
        allowed_roots=[root],
    )
    config = loader.load_stream(
        StringIO(
            "%LOADER LoadInclude\n---\n"
            "<<: !include base.yaml\n"
            "service:\n  port: 8081\n"
        ),
        source=MemorySource(root, PurePosixPath("main.yaml")),
    )
    print(json.dumps(config, indent=2, sort_keys=True))
