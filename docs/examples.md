# Worked loading examples

Runnable files are in the [`examples/` folder](../examples/README.md). Their exact
outputs are checked in [`tests/test_examples.py`](../tests/test_examples.py), while
[`tests/test_semantics.py`](../tests/test_semantics.py) checks the individual operators.

`load_yaml_file` handles ordinary YAML and the package's opt-in include
workflow. For an ordinary file, the quick start is enough:

```python
from yaml_compose import load_yaml_file

config = load_yaml_file("config.yaml")
```

Run `python examples/load_config.py` to print the simple README examples.
The [getting started example](../README.md#getting-started) uses
[base.yaml](../examples/quickstart/base.yaml),
[extra_steps.yaml](../examples/quickstart/extra_steps.yaml), and
[production.yaml](../examples/quickstart/production.yaml) to combine file
includes, deep overrides, deletion, and list spreading. The standalone
[shallow](../examples/shallow_merge.yaml),
[deep](../examples/deep_merge.yaml),
[priority](../examples/merge_precedence.yaml), and
[replacement](../examples/replace_mapping.yaml) examples isolate those rules.
Their exact outputs and the README's labeled YAML sources are checked in
`tests/test_examples.py`.

## Include a file as a value

Use `!include` to load a document into one value without merging it into its
parent mapping. These runnable example files demonstrate that form:

```yaml
# examples/include_value.yaml
%LOADER YamlCompose
---
database: !include ./database.yaml
```

```yaml
# examples/database.yaml
host: db.example.test
port: 5432
```

Loading `examples/include_value.yaml` returns:

```yaml
database:
  host: db.example.test
  port: 5432
```

## Include JSON and TOML sources

The runnable [`include_formats.yaml`](../examples/include_formats.yaml) example
uses `<<<` with a JSON object, includes a TOML table as a value, and spreads a
JSON array into a sequence. Its JSON defaults are:

```json
{
  "service": {"host": "shared.example.test", "port": 8080, "labels": ["shared"], "token": "development"},
  "logging": {"level": "info", "format": "json"}
}
```

The TOML value uses standard TOML date-time syntax:

```toml
host = "db.example.test"
port = 5432
created = 1979-05-27T07:32:00Z
```

The YAML file applies a deep override and spreads the JSON steps:

```yaml
%LOADER YamlCompose
---
<<<: !include ./include_formats/base.json
database: !include ./include_formats/database.toml
service:
  host: api.example.test
  labels: [production]
  token: !delete
steps:
  - prepare
  - !spread [!include ./include_formats/steps.json]
  - deploy
```

The resulting Python value is:

```python
{
    "service": {"host": "api.example.test", "port": 8080, "labels": ["production"]},
    "logging": {"level": "info", "format": "json"},
    "database": {
        "host": "db.example.test",
        "port": 5432,
        "created": datetime.datetime(1979, 5, 27, 7, 32, tzinfo=datetime.timezone.utc),
    },
    "steps": ["prepare", "lint", "test", "deploy"],
}
```

JSON objects, arrays, strings, numbers, booleans, and `null` keep their natural
Python values. Duplicate JSON object keys and non-finite numbers (`NaN` and
`Infinity`) are rejected because those constants are outside JSON. TOML date,
time, and datetime values remain Python
`date`, `time`, and `datetime` objects, while TOML arrays remain Python lists
inside the root table. TOML's standard `inf` and `nan` floats remain native
Python floats. JSON and TOML are selected by the resolved include source's
`.json` or `.toml` suffix; other suffixes continue to use YAML for compatibility.
JSON and TOML require UTF-8 text without a byte-order mark (BOM). A BOM is passed
to the strict format parser and reported as a marked include parse error; it is
not stripped before parsing.
Python 3.10 uses the declared `tomli` dependency; Python 3.11 and later use the
standard-library `tomllib` parser.
All included formats share the normal source-root, cycle, and resource-limit
checks. Exact outputs are asserted in
[`tests/test_examples.py`](../tests/test_examples.py) and parser failures in
[`tests/test_include_formats.py`](../tests/test_include_formats.py).

### Layer JSONC and JSON5

JSONC is useful when shared JSON defaults need comments while retaining JSON's
strict data rules. JSON5 accepts a wider configuration syntax; install its
optional parser with `pip install 'yaml-compose[json5]'`.

`defaults.jsonc`:

```jsonc
{
  // Shared service defaults.
  "service": { "port": 8080 },
  "labels": ["base"]
}
```

`local.json5`:

```json5
{
  service: { host: 'api.example.test' },
  labels: ['production',],
}
```

Compose both in YAML:

```yaml
%LOADER YamlCompose
---
<<<: [!include defaults.jsonc, !include local.json5]
```

The YAML merge keeps the inherited `service.port`, adds `service.host`, and
replaces the list:

```python
{
    "service": {"port": 8080, "host": "api.example.test"},
    "labels": ["production"],
}
```

The `.jsonc` parser rejects trailing commas, duplicate keys, and non-finite
numbers just like `.json`. JSON5 duplicate keys are rejected; its native
`NaN`/infinity values remain Python floats. See the
[format guide](formats.md) for parser and error details.

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
backend:
  mode: pooled
```

The regional file uses that base as an underlay, then overlays two service
values. The include path resolves relative to the regional file:

```yaml
# examples/region.yaml
%LOADER YamlCompose
---
<<<: !include base.yaml
service:
  host: api.us.example.test
  labels: [regional]
```

The production file uses the regional layer as its underlay. Its local values
win over the regional layer, which already includes the base:

```yaml
# examples/production.yaml
%LOADER YamlCompose
---
<<<: !include region.yaml
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
backend: remote
```

Load it with the normal path API:

```python
from yaml_compose import load_yaml_file

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
backend: remote
```

Mappings merge recursively, so the production file keeps the base service's
`nested.retries` while overriding `nested.timeout_seconds`. Lists replace the
previous list: `[regional]` replaces `[base, shared]`, then `[production]`
replaces `[regional]`. An explicit `[]` clears an inherited list. `null` is a
value and replaces the old value. `!delete` removes a key from the included
mapping entirely, so `retired` is absent from the result.
The production layer also replaces the base `backend` mapping with the scalar
`remote`, showing that overrides can change a value's type.

`!delete` takes no value. It removes a key in the mapping currently being
overlaid; a missing key is harmless. `null` instead retains the key with a null
value. The runnable `merge_modes.yaml` shows direct deletion under `<<` and
nested deletion under `<<<`:

```yaml
shallow_delete:
  <<: *defaults
  enabled: !delete
deep_delete:
  <<<: *defaults
  service:
    port: !delete
```

The shallow result keeps `service` but has no `enabled` key. The deep result
keeps `service.host` but has no `service.port`. A nested `!delete` under `<<`
is invalid: a local nested mapping replaces the inherited mapping and has no
overlay at that level. `!delete` is also invalid as a standalone value, list
item, or mapping key.

These layers also show relative lookup: `production.yaml` resolves
`region.yaml`, and `region.yaml` resolves `base.yaml` from the directory of the
file that contains each `!include`. The current working directory is not used
as an implicit search root.

## Configurable include lookup

The runnable [`include_search.py`](../examples/include_search.py) compares
four lookup policies against the same nested configuration. Configure one
with `YamlConfigLoader(include_search="search-first", search_paths=[...])`,
or pass the same keywords directly to a loading function.

| Policy         | Bare-name lookup order                                    |
| -------------- | --------------------------------------------------------- |
| `local-first`  | Containing file's directory, then configured search paths |
| `search-first` | Configured search paths, then containing file's directory |
| `local-only`   | Containing file's directory                               |
| `search-only`  | Configured search paths                                   |

`local-first` is the default. With `local-only` or `search-only`, a match in
the excluded group is not used. In filesystem lookup, an empty search-path
list gives `search-only` no candidates for a bare name. Allowed roots authorize
files but do not add lookup directories. The memory resolver instead uses
its namespace root as its search location, and the same policies select
between that root and the containing virtual file's directory.

The example's [`main.yaml`](../examples/search/app/main.yaml) uses a pinned
file reference:

```yaml
%LOADER YamlCompose
---
service: !include ./nested/service.yaml
```

Its [`service.yaml`](../examples/search/app/nested/service.yaml) inherits
defaults and builds a sequence from another included file:

```yaml
<<<: !include defaults.yaml
port: 9090
steps:
  - prepare
  - !spread [!include steps.yaml]
  - package
pinned: !include ./identity.yaml
```

The directories beside the service file and in `search/shared` both contain
`defaults.yaml`, `steps.yaml`, and `identity.yaml`. Local-first and local-only
select the nearby defaults and list items. Search-first and search-only
select the shared defaults and list items. All four select the local
`identity.yaml`, because an explicit `./` reference is anchored to its
containing file and does not search elsewhere.

In every mode the local `port: 9090` overrides the selected defaults. The
host and limits are inherited from the selected file. `steps` is always
`[prepare, local-check, package]` or `[prepare, shared-check, package]`.
Its insertion order is independent of file lookup policy. Exact values for
all four modes are checked in `tests/test_examples.py`.

An explicit `../shared.yaml` reference is likewise anchored to the containing
file's parent directory. Windows `./` and `../` spellings using backslashes
work as native filesystem paths too. Missing explicit relative files are
errors; search directories are not used as fallbacks. A stream must supply
`source_path` or a resolver-defined `source` for explicit relative references.
Absolute filesystem paths name one exact file independently of lookup policy.
Every resolved file must still remain within the configured allowed roots.

## Merge modes and explicit replacement

The runnable [`merge_modes.yaml`](../examples/merge_modes.yaml) compares
shallow `<<` and deep `<<<` with both an anchor and an included file. Each
mapping may contain only one merge key. Its
[`merge_defaults.yaml`](../examples/merge_defaults.yaml) source contains the
same mapping as the `defaults` anchor:

```yaml
%LOADER YamlCompose
---
defaults: &defaults
  service: {host: localhost, port: 8080}
  enabled: true
shallow_anchor: {<<: *defaults, service: {port: 8081}}
shallow_file: {<<: !include merge_defaults.yaml, service: {port: 8081}}
deep_anchor: {<<<: *defaults, service: {port: 8081}}
deep_file: {<<<: !include merge_defaults.yaml, service: {port: 8081}}
```

Both shallow results contain `service: {port: 8081}`. Both deep results retain
`host: localhost` alongside `port: 8081`. The same runnable file uses
`<<: [*defaults, !include merge_later.yaml]` and the corresponding `<<<` form
to show that the earlier source has higher priority than the later source,
then explicit local values win. In both results `enabled` is `true` from the
earlier source. The deep result combines `host`, `port`, and the later source's
`timeout` in `service`; the shallow result keeps only the local `port` there.
Both merge keys follow YAML's first-source-wins priority rule. Moving a merge
key above or below local keys does not change which values win.

When both values are mappings, a deep merge preserves inherited keys. Use
`!replace` to discard them at one key:

```yaml
replace_inline: {<<<: *defaults, service: !replace {port: 9000}}
replace_anchor: {<<<: *defaults, service: !replace [*replacement]}
replace_file: {<<<: *defaults, service: !replace [!include merge_replacement.yaml]}
clear_mapping: {<<<: *defaults, service: !replace {}}
```

Each `replace_*` result has `service: {port: 9000}`; `clear_mapping` has
`service: {}`. In the runnable file, `replacement` is an anchor containing
`{port: 9000}`, and [`merge_replacement.yaml`](../examples/merge_replacement.yaml)
contains the same mapping. The brackets contain exactly one mapping source.
`!replace` is valid only for a value in a deep overlay; if that key is absent
from the underlay, its mapping is used as-is.

## List spread

To insert sequence items at a particular position, use `!spread` with one
sequence source in brackets. That source may be a same-document alias or an
`!include` file. The runnable files `examples/spread_sources.yaml` and
`examples/extra-items.yaml` show both sources:

```yaml
%LOADER YamlCompose
---
shared: &shared [alpha, beta]
items:
  - before
  - !spread [*shared]
  - !spread [!include ./extra-items.yaml]
  - after
```

If `extra-items.yaml` contains `[remote-a, remote-b]`, `items` becomes
`[before, alpha, beta, remote-a, remote-b, after]`. Each operator unpacks its
source at its position, like Python's `*` inside a list literal. Neither use
needs a mapping underlay. The anchor must be defined earlier in the document,
and both sources must be sequences. The brackets contain exactly one source;
`!spread [a, b]` is invalid because it names two scalars.

The three-layer example chains base, region, and production with `<<<` because
each layer preserves nested settings from the previous layer.

## In-memory source

When configuration comes from an application-owned mapping rather than files,
configure a `MemoryIncludeResolver` and identify the top-level stream with a
`MemorySource`:

```python
from io import StringIO
from pathlib import PurePosixPath

from yaml_compose import (
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
                "%LOADER YamlCompose\n---\n"
                "<<<: !include ../shared/base.yaml\n"
                "service:\n  port: 8081\n"
            ),
        },
    ),
    allowed_roots=[root],
)
config = loader.load_stream(
    StringIO("%LOADER YamlCompose\n---\n<<<: !include environments/production.yaml\n"),
    source=MemorySource(root, PurePosixPath("configs/main.yaml")),
)
```

The same relative-include rules apply inside the virtual namespace. The
resolver has no network backend; an application-supplied resolver is trusted
code and must honor the roots in each request. The executable version is
[`examples/in_memory_config.py`](../examples/in_memory_config.py).
It resolves to `{"service": {"host": "localhost", "port": 8081, "labels": ["base"]}}`.

## Caller-selected YAML loader

The runnable [`custom_loader.py`](../examples/custom_loader.py) extends
`yaml.SafeLoader` with `!upper` and passes that class as `yaml_loader` to
`load_yaml_file`. Its top-level file includes
[`custom_loader_profile.yaml`](../examples/custom_loader_profile.yaml), which
contains `name: !upper alice`. The selected loader applies inside the included
file too, producing `{"profile": {"name": "ALICE"}}`. Without that selected
loader, the application tag is rejected. The script checks both the default
recursive composition mode and `recursive_dispatch=False`; in the latter mode,
the selected loader parses the included file as ordinary YAML.

## Explicitly unbounded limits

The runnable [`include_limits.py`](../examples/include_limits.py) loads the
three-layer production example with `max_include_files=None`. That disables
only the included-file count bound. Its example test first shows that the same
layers exceed `max_include_files=1`, then checks the unbounded result. The
depth, per-file size, and total-byte limits remain at their defaults unless
set individually to `None`.

## Intentional error

`!delete` is only meaningful while applying a mapping overlay. Here there is
no inherited `service` mapping, so it raises `YamlIncludeError`:

```yaml
%LOADER YamlCompose
---
service:
  token: !delete
```

Duplicate explicit keys are also rejected, even when the document does not
opt into include behavior:

```yaml
name: first
name: second
```
