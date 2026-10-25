# yaml-compose

Compose YAML configuration with file includes, recursive mapping overrides, and
positional list spreading. Loading returns ordinary Python values.

| Syntax               | Behavior                                                  |
| -------------------- | --------------------------------------------------------- |
| `!include file.yaml` | Loads another YAML file as a value.                       |
| `<<`                 | Shallow merge; local mappings replace inherited mappings. |
| `<<<`                | Deep merge; nested mappings combine recursively.          |
| `!delete`            | Removes an inherited key during an overlay.               |
| `!replace`           | Replaces a mapping value during deep merge.               |
| `!spread [source]`   | Inserts sequence items at its position.                   |

These choices follow established YAML behavior where possible. The
[design notes](docs/design-notes.md#why-these-semantics) explain the rationale and
distinguish compatibility behavior from this package's extensions.

## Quick start

Install from the repository:

```shell
python -m pip install .
```

These files are runnable examples in [`examples/quickstart/`](examples/README.md).

### Base configuration

```yaml
# examples/quickstart/base.yaml
service:
  host: localhost
  port: 8080
  labels: [internal]
  token: development-token
steps: [legacy]
```

### Sequence source

```yaml
# examples/quickstart/extra_steps.yaml
- lint
- test
```

### Production overlay

```yaml
# examples/quickstart/production.yaml
%LOADER YamlCompose
---
<<<: !include ./base.yaml
service:
  host: api.example.test
  labels: [production]
  token: !delete
steps:
  - prepare
  - !spread [!include ./extra_steps.yaml]
  - deploy
```

### Load

```python
from yaml_compose import load_yaml_file

config = load_yaml_file("examples/quickstart/production.yaml")
```

### Result: composed configuration

```yaml
service:
  host: api.example.test
  port: 8080
  labels: [production]
steps: [prepare, lint, test, deploy]
```

The port is inherited, the token is absent, and both local lists replace their inherited
lists. The spread inserts `lint` and `test` between `prepare` and `deploy`.

`%LOADER YamlCompose` opts a document into composition. Files without this header use
ordinary YAML loading with duplicate explicit keys rejected. The default parser is
PyYAML's `SafeLoader`. Included files use composition by default; set
`recursive_dispatch=False` to parse them as ordinary YAML instead.

## Include a value and choose where to find it

An include can supply any YAML value without merging it into the parent mapping.

### Included file

```yaml
# examples/database.yaml
host: db.example.test
port: 5432
```

### Composition document

```yaml
# examples/include_value.yaml
%LOADER YamlCompose
---
database: !include ./database.yaml
```

### Result: included value

```yaml
database:
  host: db.example.test
  port: 5432
```

Anchors and aliases reuse nodes within a YAML document; they do not load another file.
`!include` makes cross-file loading explicit.[^yaml-model]

Explicit `./` and `../` paths resolve from the file containing that include, including
at nested levels. A missing explicit relative file is an error; there is no search-path
fallback. Absolute filesystem paths identify one exact file.

For names without an explicit relative prefix, such as `database.yaml` or
`services/database.yaml`, choose a lookup policy:

| `include_search` | Candidate directories, in order                           |
| ---------------- | --------------------------------------------------------- |
| `local-first`    | Containing file's directory, then configured search paths |
| `search-first`   | Configured search paths, then containing file's directory |
| `local-only`     | Containing file's directory                               |
| `search-only`    | Configured search paths                                   |

The default is `local-first`. Search directories keep their supplied order. Nested
includes inherit the policy and use their own containing file's directory.

```python
from yaml_compose import YamlConfigLoader

loader = YamlConfigLoader(
    search_paths=["config/shared"],
    include_search="search-first",
)
config = loader.load_file("config/production.yaml")
```

The same `include_search` keyword is available on `load_yaml_file`,
`load_yaml_dispatch`, and `load_yaml_compose`. The
[search example](examples/include_search.py) demonstrates all four policies with
nested includes, deep overrides, list spreads, and a pinned `./identity.yaml` reference.

Lookup order selects a file; merge precedence determines which loaded values win.
Changing one does not change the other. The working directory is never an implicit
lookup location. Allowed roots default to the top-level file's directory plus configured
search paths; explicit `allowed_roots` replace that set. Roots restrict access, including
after symlink resolution, and do not add search locations.

## Shallow and deep mapping merges

Both merge operators accept a mapping alias, an included mapping, or a sequence of
mapping sources. These examples share the same defaults.

### Shared mapping

```yaml
# examples/merge_defaults.yaml
service: {host: localhost, port: 8080}
enabled: true
```

### Shallow merge with `<<`

```yaml
# examples/shallow_merge.yaml
%LOADER YamlCompose
---
<<: !include ./merge_defaults.yaml
service:
  port: 8081
```

### Result: shallow merge

```yaml
service:
  port: 8081
enabled: true
```

### Deep merge with `<<<`

```yaml
# examples/deep_merge.yaml
%LOADER YamlCompose
---
<<<: !include ./merge_defaults.yaml
service:
  port: 8081
```

### Result: deep merge

```yaml
service:
  host: localhost
  port: 8081
enabled: true
```

Lists, scalars, and `null` replace inherited values in either mode. An empty list clears
an inherited list. Under a deep merge, an empty local mapping preserves inherited keys;
use `!replace {}` to clear that mapping.

### Source priority and local overrides

Both operators give **earlier merge sources higher priority**. Explicit local values
win over every source, regardless of where the merge key appears in the mapping.[^merge-priority]
`<<<` deliberately carries the `<<` priority rule into recursive mapping merges.

#### Document

```yaml
# examples/merge_precedence.yaml
%LOADER YamlCompose
---
first: &first {winner: first, service: {host: localhost, port: 8080}}
second: &second {winner: second, service: {port: 9000, timeout: 30}}
merged:
  service: {port: 9090}
  <<<: [*first, *second]
```

#### Result: full document

```yaml
first:
  winner: first
  service:
    host: localhost
    port: 8080
second:
  winner: second
  service:
    port: 9000
    timeout: 30
merged:
  winner: first
  service:
    host: localhost
    port: 9090
    timeout: 30
```

The second source supplies the missing timeout. The explicit local port wins even though
it appears before `<<<`. YAML mappings have no key order; source priority is expressed by
the ordered sequence of sources.[^yaml-model]

A source sequence is a priority list, not successive Python dictionary updates. Mapping
key position does not determine precedence. Each mapping accepts one merge key;
duplicate explicit keys are errors. Anchor declarations are ordinary mapping entries,
so `first` and `second` also remain in the returned value. Anchors are document-local.

`<<` preserves PyYAML's established shallow merge behavior. `<<<` is this package's
recursive counterpart, available in composition mode. The
[merge modes example](examples/merge_modes.yaml) covers anchor and file sources together.

## Spread list items at their position

Use a sequence source to insert items from an anchor or a file.

### Included sequence

```yaml
# examples/extra-items.yaml
- remote-a
- remote-b
```

### Composition document

```yaml
# examples/spread_sources.yaml
%LOADER YamlCompose
---
shared: &shared [alpha, beta]
items:
  - before
  - !spread [*shared]
  - !spread [!include ./extra-items.yaml]
  - after
```

### Result: spread sequence

```yaml
shared: [alpha, beta]
items: [before, alpha, beta, remote-a, remote-b, after]
```

Each spread unpacks one level at its position in the ordered YAML sequence, preserving order
and duplicates.[^yaml-model] It does not require a mapping merge. The brackets contain
exactly one sequence source. An empty source inserts no items.

A regular `!include` supplies one value. If that value is a list inside another list,
it stays nested until explicitly spread. A local list always replaces an inherited
list; spreading constructs the replacement list without automatically appending to the old one.

## Remove or replace inherited mapping entries

`!delete` removes a mapping key during an overlay and does nothing for a missing key.
It takes no value. `null` keeps the key and sets its value to null.
`!delete` and `!replace` are package-defined tags applied only while an overlay is being
constructed; `null` remains an ordinary YAML value.

### Delete overlay

```yaml
%LOADER YamlCompose
---
base: &base {token: secret, clear: old}
result:
  <<<: *base
  token: !delete
  clear: null
```

### Result: deletion

```yaml
base: {token: secret, clear: old}
result: {clear: null}
```

Nested deletion requires an inherited mapping at that level under `<<<`; a shallow local
mapping replaces its source and does not provide a nested overlay. The
[merge modes example](examples/merge_modes.yaml) and
[`test_semantics.py`](tests/test_semantics.py) cover deletion at both merge depths.

Use `!replace` to replace one mapping value during a deep merge.

### Replacement overlay

```yaml
# examples/replace_mapping.yaml
%LOADER YamlCompose
---
<<<: !include ./merge_defaults.yaml
service: !replace {port: 9000}
```

### Result: replacement

```yaml
service:
  port: 9000
enabled: true
```

The inherited host is gone. `!replace {}` clears a mapping. An alias or file can supply
the replacement through `!replace [*replacement]` or
`!replace [!include replacement.yaml]`.

These controls apply while constructing the file's own overlay. Every included file
returns a completed value; deletion and replacement instructions are not carried into
future merges. The [three-layer example](docs/examples.md#three-filesystem-layers)
shows successive files overriding inherited configuration.

## Loading policies and extension points

For repeated loads, reuse `YamlConfigLoader`. Each call gets a fresh include budget.
The defaults are 16 include levels, 64 included files, 1 MiB per included file, and
8 MiB of total included content. Set an individual `max_*` argument to `None` to leave
that resource unbounded; the top-level input is not byte-limited.

- [Include limits](examples/include_limits.py) shows an explicitly unbounded file count.
- [Custom YAML loaders](examples/custom_loader.py) demonstrates `yaml_loader` for
  application-defined tags, including tags in included files.
- [In-memory includes](examples/in_memory_config.py) demonstrates a custom include
  source through `IncludeResolver`. Custom resolvers receive the selected search policy.

Use `load_yaml_dispatch` for a text stream with header dispatch or `load_yaml_compose`
to select composition explicitly. Streams using includes need a source or configured
lookup locations and allowed roots. Explicit relative references require `source_path`
or a resolver-defined `source`.

The loader reports YAML source locations and include failures. Comments, formatting,
and per-key provenance are not retained in the returned Python values. The
[worked examples](docs/examples.md) and [design notes](docs/design-notes.md) cover
resolution, resource bounds, and additional cases.

## Run the examples and checks

Run the simple README examples and compare include lookup policies:

```shell
python examples/load_config.py
python examples/include_search.py
```

The [examples index](examples/README.md) links every runnable example. Their results
are checked in [test_examples.py](tests/test_examples.py); labeled YAML blocks in this
README are also checked against the corresponding files to prevent documentation drift.
The [composition tests](tests/test_composition.py) exercise nested includes,
search precedence, overlays, and spreads together, including error and budget boundaries.

## Run CI locally

Create a virtual environment and install the development tools once:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Then run the same quality checks used by GitHub Actions:

```powershell
.\.venv\Scripts\python.exe -m scripts.check_local
```

The command runs lint, formatting, type checks, tests with coverage, Markdown checks,
pre-commit hooks, and package build checks. It works from PowerShell without activating
the environment. On macOS or Linux, replace `.venv\Scripts\python.exe` with
`.venv/bin/python`. The CI workflow calls this same command for its quality job; its
separate test job also checks the supported Python versions on Windows and Linux.

Python 3.10 and newer are supported. CI covers Windows and Linux with Python 3.10
through 3.15, plus lint, type, documentation, coverage, and package checks.

[^yaml-model]: The [YAML 1.2.2 specification](https://yaml.org/spec/1.2.2/) defines
sequences as ordered and mappings as unordered collections of unique keys. Anchors and
aliases refer to nodes in a YAML representation; they are not file imports.

[^merge-priority]: The [YAML 1.1 merge-key working draft](https://yaml.org/type/merge.html)
defines earlier mappings in a `<<` source sequence as higher priority and preserves keys
already present in the current mapping. YAML 1.2 [removed the special `<<` key](https://yaml.org/spec/1.2.2/ext/changes/);
PyYAML retains it in [`SafeConstructor.flatten_mapping`](https://github.com/yaml/pyyaml/blob/main/lib/yaml/constructor.py#L1989-L2066).
`yaml-compose` carries that precedence into its package-specific `<<<` operator.
