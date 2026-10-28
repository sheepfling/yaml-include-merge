# yaml-compose

**Compose configuration with YAML.**

`yaml-compose` lets YAML files share defaults and describe how values combine. It adds
explicit include and overlay operations while keeping the result as ordinary Python data.

## Getting started

Install from a checkout:

```shell
python -m pip install .
```

Python 3.10 or later is required.

Create a base configuration:

```yaml
# base.yaml
service:
  host: localhost
  port: 8080
  labels: [internal]
  token: development-token

logging:
  level: info
```

Then compose it with environment-specific values:

```yaml
# production.yaml
%LOADER YamlCompose
---
<<<: !include base.yaml

service:
  host: api.example.test
  labels: [production, metrics]
  token: !delete
```

Load it with the usual Python call:

```python
from yaml_compose import load_yaml_file

config = load_yaml_file("production.yaml")
```

The included port and logging settings are retained, local values override the base, and
`token` is removed. The `%LOADER YamlCompose` header opts this file into composition;
ordinary YAML files can still be loaded as ordinary YAML.

## How composition works

- `<<` performs PyYAML's familiar shallow mapping merge; `<<<` recursively merges mappings.
- Explicit local keys override inherited values. When several sources are listed, earlier
  sources take precedence over later ones.
- Lists replace inherited lists. Use `!spread` to insert items from a sequence where they
  belong.
- `!delete` removes an inherited key. `null` keeps the key with a null value.
- `!replace` replaces an inherited mapping instead of merging it; `!replace {}` clears it.

These are `yaml-compose` operations, not YAML-standard merge directives. YAML provides
application-specific tag syntax, but it does not define these tags or the `<<<` key. The
package-specific header uses YAML directive syntax; other YAML tools do not apply these
composition rules.

See [worked examples](docs/examples.md) for merge precedence, nested includes, spreads,
replacement, deletion, and list behavior. The [design notes](docs/design-notes.md) explain
how the composition rules relate to YAML and PyYAML.

## Include other files

Use `!include` for structured data. The resolved filename selects the parser; YAML remains
the composition layer, so included mappings can participate in the same merge operations.

| Tag              | Input                                | Result                              |
| ---------------- | ------------------------------------ | ----------------------------------- |
| `!include`       | YAML, JSON-family, TOML, INI, dotenv | Native Python value                 |
| `!include-table` | CSV or registered table adapter      | Rows of strings; first row included |
| `!include-text`  | UTF-8 text                           | Exact decoded string                |
| `!include-raw`   | Any file                             | Exact bytes                         |

JSON5 is optional; install `yaml-compose[json5]` to use it. JSON Lines and NDJSON produce
ordered lists of records. CSV needs no Python setup and does not infer headers or convert
cell values. Custom table adapters, such as a caller-supplied TSV parser, can also use
`!include-table`; no other table formats are guessed. TOML date and time values remain Python
`date`, `time`, or `datetime` objects.
Raw includes return bytes, which need explicit conversion before JSON serialization.

For parser details and the format adapter API, see the [included formats guide](docs/formats.md).
The [worked format examples](docs/examples.md#include-json-and-toml-sources) cover JSON,
TOML, JSONC, JSON5, JSON Lines, NDJSON, and CSV. See the [INI, dotenv, and text examples](docs/examples.md#ini-dotenv-and-text-includes)
for string, text, and raw includes.

## Loading and include boundaries

`load_yaml_file` handles both ordinary YAML and documents opted into composition. Use
`load_yaml_compose` when you want to explicitly select composition. For repeated loads or
shared policies, configure a `YamlConfigLoader` once. The default parser uses PyYAML's
`SafeLoader`; applications can provide a trusted custom loader for their own YAML tags.

Filesystem includes resolve relative to the file containing the reference. The default
resolver is local-only; it does not fetch URLs. Resolved files must stay within allowed roots,
and configurable limits bound include depth, file count, per-file size, and total included
bytes. The same checks apply to YAML, structured formats, text, raw bytes, and CSV tables.
Applications can supply an `IncludeResolver` for their own trusted sources; see the
[format and adapter guide](docs/formats.md) and [in-memory example](examples/in_memory_config.py).

Loading returns values, not an editable YAML syntax tree: comments, formatting, and per-key
source provenance are not retained. PyYAML's scalar resolution rules remain in effect, and
explicit duplicate mapping keys are rejected.

When a nested include fails, the original PyYAML exception type and leaf mark are preserved.
The error prints the route from the root include site to the failing source. Call
`get_include_route(error)` for a typed tuple of `IncludeRouteFrame` values; each frame has the
including source, one-based line and column, tag, requested path, and resolved source when
available.

## Explore

- The [examples index](examples/README.md) lists runnable examples and expected results;
  [tests/test_examples.py](tests/test_examples.py) verifies those results.
- [Included formats and custom adapters](docs/formats.md)
- [Worked loading examples](docs/examples.md)
- [Composition design notes](docs/design-notes.md)

## Development

Install the development tools and run the repository checks:

```shell
python -m pip install -e ".[dev]"
python -m scripts.ci
```

The package supports Python 3.10 and later. The [examples tests](tests/test_examples.py)
check the documented results.

## License and release notes

`yaml-compose` is distributed under the [MIT license](LICENSE). See the
[changelog](CHANGELOG.md) for release notes and the [release guide](docs/releasing.md)
for the publishing process.
