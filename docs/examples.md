# Worked loading examples

`load_yaml_file` handles ordinary YAML and the package's opt-in include
workflow. For an ordinary file, the quick start is enough:

```python
from yaml_include_merge import load_yaml_file

config = load_yaml_file("config.yaml")
```

## Three filesystem layers

These files live in `examples/`. The first file provides defaults:

```yaml
# examples/base.yaml
service:
  host: 127.0.0.1
  port: 8080
  labels: [base, shared]
  clearable: [legacy]
  token: example-token
  retired: true
  nested:
    retries: 3
    timeout_seconds: 30
logging:
  level: info
  format: json
```

The regional file includes that base relative to its own location, then
overrides two service values:

```yaml
# examples/region.yaml
%LOADER LoadInclude
---
<<: !include base.yaml
service:
  host: api.us.example.test
  labels: [regional]
```

The production file includes the regional layer. Its values win over the
regional layer, which already includes the base:

```yaml
# examples/production.yaml
%LOADER LoadInclude
---
<<: !include region.yaml
service:
  port: 8081
  labels: [production]
  clearable: []
  token: null
  retired: !delete
  nested:
    timeout_seconds: 45
logging:
  level: warning
```

Load it with the normal path API:

```python
from yaml_include_merge import load_yaml_file

config = load_yaml_file("examples/production.yaml")
```

The exact result is:

```yaml
service:
  host: api.us.example.test
  port: 8081
  labels: [production]
  clearable: []
  token: null
  nested:
    retries: 3
    timeout_seconds: 45
logging:
  level: warning
  format: json
```

Mappings merge recursively. Lists replace the previous list, so
`[production]` replaces `[regional]`; an explicit `[]` clears an inherited
list. `null` is a value and replaces the old value. `!delete` removes a key
from the included mapping. These layers also show relative lookup:
`production.yaml` resolves `region.yaml`, and `region.yaml` resolves
`base.yaml` from the directory of the file that contains each `!include`.
The current working directory is not used as an implicit search root.

Each mapping supports one `<<: !include` base. To combine more layers, chain
files as shown here: base, then region, then production. A second `<<` in the
same mapping is an error.

## In-memory source

When configuration comes from an application-owned mapping rather than files,
configure a `MemoryIncludeResolver` and identify the top-level stream with a
`MemorySource`:

```python
from io import StringIO
from pathlib import PurePosixPath

from yaml_include_merge import (
    MemoryIncludeResolver,
    MemoryRoot,
    MemorySource,
    YamlConfigLoader,
)

root = MemoryRoot("demo-config")
loader = YamlConfigLoader(
    include_resolver=MemoryIncludeResolver(
        root,
        {
            "configs/shared/base.yaml": "service:\n  host: localhost\n  port: 8000\n",
            "configs/environments/production.yaml": (
                "%LOADER LoadInclude\n---\n"
                "<<: !include ../shared/base.yaml\n"
                "service:\n  port: 8081\n"
            ),
        },
    ),
    allowed_roots=[root],
)
config = loader.load_stream(
    StringIO("%LOADER LoadInclude\n---\n<<: !include environments/production.yaml\n"),
    source=MemorySource(root, PurePosixPath("configs/main.yaml")),
)
```

The same relative-include rules apply inside the virtual namespace. The
resolver has no network backend; an application-supplied resolver is trusted
code and must honor the roots in each request. The executable version is
[`examples/in_memory_config.py`](../examples/in_memory_config.py).

## Intentional error

`!delete` is only meaningful while applying an include overlay. This ordinary
mapping has no included base, so it raises `YamlIncludeError`:

```yaml
%LOADER LoadInclude
---
service:
  token: !delete
```
