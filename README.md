# yaml-compose

**YAML, with a few more ways to compose it.**

YAML is good at describing configuration. Reusing that configuration is where things tend to get complicated.

A common base, a few environment-specific changes, an inherited value you no longer want,
or a list you'd rather extend than repeat—none of these should require introducing a
template language.

`yaml-compose` adds a small set of opt-in composition semantics to YAML:

- **`<<<`** recursively merges mappings, extending YAML's familiar `<<`.
- **`!include`** brings another YAML file into the document.
- **`!delete`** removes a key during an overlay.
- **`!replace`** explicitly replaces a mapping instead of merging it.
- **`!spread`** inserts elements from another sequence.

The syntax stays close to YAML. The results are ordinary Python values.

## See it in action

Suppose you start with a shared configuration:

**`base.yaml`**

```yaml
service:
  host: localhost
  port: 8080
  labels: [internal]
  token: development-token

logging:
  level: info
```

Production needs a different host and set of labels, and shouldn't inherit the development token.

**`production.yaml`**

```yaml
%LOADER YamlCompose
---
<<<: !include base.yaml

service:
  host: api.example.test
  labels: [production, metrics]
  token: !delete
```

The composed result is:

```yaml
service:
  host: api.example.test
  port: 8080
  labels: [production, metrics]

logging:
  level: info
```

The `port` and `logging` settings are inherited. The `host` and `labels` are overridden.
The `token` is removed entirely.

Nothing needs to know where a particular value came from after loading. The configuration
is simply a mapping.

```python
from yaml_compose import load_yaml_file

config = load_yaml_file("production.yaml")
```

The `%LOADER YamlCompose` directive opts the document into composition. Ordinary YAML files
can still be loaded without modification.

## Composition that reads like YAML

Rather than introducing conditionals, interpolation expressions, or a new configuration
format, `yaml-compose` builds on structures YAML already has: mappings, sequences, scalars,
anchors, and aliases.

### Merge shallowly or recursively

YAML already supports the `<<` merge key. It merges mappings at one level, allowing local
keys to override inherited ones.

`yaml-compose` introduces its recursive counterpart, `<<<`.

```yaml
%LOADER YamlCompose
---
defaults: &defaults
  service:
    host: localhost
    port: 8080

shallow:
  <<: *defaults
  service:
    port: 8081

deep:
  <<<: *defaults
  service:
    port: 8081
```

The distinction is straightforward:

```yaml
shallow:
  service:
    port: 8081

deep:
  service:
    host: localhost
    port: 8081
```

With `<<`, the local `service` mapping replaces the inherited mapping.

With `<<<`, the two `service` mappings combine recursively.

Both operators work with YAML aliases and included files:

```yaml
<<: *defaults
<<: !include defaults.yaml

<<<: *defaults
<<<: !include defaults.yaml
```

The source changes; the merge semantics do not.

### Delete what you don't want

Sometimes overriding a value isn't enough. You want it gone.

```yaml
service:
  token: !delete
```

During a mapping overlay, `!delete` removes the key rather than assigning a new value.

This is deliberately different from `null`:

```yaml
token: null      # The key exists with a null value
token: !delete   # The key is removed
```

With recursive merging, deletion also works inside nested mappings.

### Replace when merging isn't appropriate

Deep merging is useful until you want an entirely new mapping.

`!replace` provides an explicit escape hatch:

```yaml
service: !replace
  port: 9090
```

Under `<<<`, this discards the inherited `service` mapping and replaces it with one
containing only `port`.

You can also write `!replace {}` to clear a mapping, or use an existing mapping as the replacement:

```yaml
service: !replace [*replacement]
service: !replace [!include replacement.yaml]
```

No special precedence trick is needed. The YAML says exactly what should happen.

### Spread sequences without changing their defaults

Sequences follow a simpler rule: **a local sequence replaces an inherited sequence**.

That keeps recursive merging predictable. An empty list, `[]`, clears an inherited list.

When you actually want to insert items from another sequence, use `!spread`.

```yaml
%LOADER YamlCompose
---
common: &common [lint, test]

steps:
  - prepare
  - !spread [*common]
  - package
```

The result is:

```yaml
common: [lint, test]

steps:
  - prepare
  - lint
  - test
  - package
```

The same syntax works with a YAML file containing a sequence:

```yaml
steps:
  - prepare
  - !spread [!include common-steps.yaml]
  - package
```

The insertion happens exactly where `!spread` appears.

### Predictable precedence

Composition follows a few consistent rules:

- Explicit local keys win over inherited values.
- When multiple merge sources are supplied, earlier sources win over later ones.
- Recursive merging combines mappings with mappings; other local values replace inherited values.
- Lists replace by default, unless elements are explicitly inserted with `!spread`.
- A mapping uses one merge key, which may accept a sequence of sources.

For example:

```yaml
<<<:
  - !include environment.yaml
  - !include defaults.yaml

service:
  port: 9090
```

Here, `environment.yaml` has precedence over `defaults.yaml`, while the local `service.port`
takes precedence over both.

## Includes that follow the configuration

An include should behave like a reference to another file—not like a path whose meaning
depends on where Python happened to be started.

`yaml-compose` resolves include paths **relative to the YAML file containing the include**.

Consider:

```text
config/
├── app.yaml
├── defaults.yaml
└── services/
    ├── api.yaml
    └── logging.yaml
```

In `app.yaml`:

```yaml
%LOADER YamlCompose
---
<<<: !include defaults.yaml

service: !include services/api.yaml
```

And in `services/api.yaml`:

```yaml
%LOADER YamlCompose
---
<<<: !include logging.yaml

host: localhost
```

The nested include resolves beside `api.yaml`, not beside `app.yaml`.

This means configuration files can naturally refer to their neighbors, even when included
several levels deep.

### Search paths when you need them

Sometimes configurations share a common library of reusable files.

For those cases, you can supply additional search paths:

```python
from pathlib import Path
from yaml_compose import YamlConfigLoader

loader = YamlConfigLoader(
    search_paths=[Path("config/shared")]
)

development = loader.load_file("config/development.yaml")
production = loader.load_file("config/production.yaml")
```

Resolution follows a consistent order:

1. Try the directory of the YAML file containing the include.
2. If nothing is found there, try configured search paths in order.
3. Require resolved files to remain within the allowed roots.

The process's current working directory is never used as an implicit fallback.

By default, the top-level file's directory and configured search paths define the allowed
roots. You can supply `allowed_roots` explicitly when a configuration needs access to a
broader or different directory layout.

These boundaries also apply after resolving symlinks. A file found outside an allowed root
is an error, not a reason to silently try the next candidate.

The goal is simple: **a configuration should resolve the same way regardless of where the
application is launched.**

## Includes don't have to come from files

The `!include` syntax describes the relationship between configuration values. It doesn't
have to dictate where those values are stored.

Filesystem resolution is the default, but applications can inject their own implementation
of the `IncludeResolver` protocol.

This makes it possible to supply YAML from application-managed or virtual sources without
changing the configuration syntax.

The package includes a `MemoryIncludeResolver` for in-memory configuration:

[See the in-memory configuration example](examples/in_memory_config.py).

Custom resolvers are trusted application code and are responsible for respecting the
requested resolution boundaries. The built-in resolvers are local-only and do not fetch
network URLs.

## Getting started

Install from the repository:

```shell
python -m pip install .
```

For most applications, loading a configuration takes one call:

```python
from yaml_compose import load_yaml_file

config = load_yaml_file("config.yaml")
```

`load_yaml_file` handles both ordinary YAML and documents opted into composition.

For repeated loads or shared include policies, configure `YamlConfigLoader` once.

You can also explicitly choose composition with `load_yaml_compose`, or supply a custom
PyYAML loader through `yaml_loader` when your application defines additional trusted YAML
tags.

By default, parsing uses PyYAML's `SafeLoader`.

## A small extension, not a new YAML language

Composition is intentionally narrow.

There is no separate expression language, no new type system, and no requirement to migrate
existing configuration files. The added operators are explicit, and the result remains
ordinary Python data.

A few boundaries are worth knowing:

- **Composition is opt-in.** `%LOADER YamlCompose` activates the extended behavior for a
  document. Included files use composition by default; `recursive_dispatch=False` changes
  how nested files are parsed.
- **Includes are bounded.** Configurable limits constrain include depth, file count, file
  size, and total included bytes. Defaults are 16 levels, 64 included files, 1 MiB per
  file, and 8 MiB total.
- **Errors retain context.** YAML errors preserve source locations, while include failures
  identify the reference and the failed path or resolution policy.
- **Output is data, not an editable document tree.** Comments, formatting, and per-key
  source provenance are not retained.
- **PyYAML's scalar rules remain in effect.** This package does not change implicit scalar
  resolution to YAML 1.2 behavior. Duplicate explicit mapping keys are rejected.

For details on the grammar, operators, and implementation choices, see the [design notes](docs/design-notes.md).

Use an immutable `IncludeLimits` profile to share bounds across loads. Pass it to
`YamlConfigLoader` or to `load_yaml_file`, `load_yaml_dispatch`, or `load_yaml_compose`:

```python
from yaml_compose import DEFAULT_INCLUDE_LIMITS, IncludeLimits, YamlConfigLoader

profile = IncludeLimits(max_include_depth=8, max_include_files=24)
loader = YamlConfigLoader(include_limits=profile)
```

`DEFAULT_INCLUDE_LIMITS` exposes the package baseline. `set_default_include_limits` can
change the process-wide defaults for new loads; each loader captures its limits when it is
created. Existing `max_*` arguments override only their corresponding profile fields, and
an explicit `None` leaves that bound unlimited.

## Examples

The repository includes runnable examples covering the main composition behaviors:

- [Merge modes](examples/merge_modes.yaml) — shallow and recursive merges, using aliases and files
- [In-memory includes](examples/in_memory_config.py) — application-owned include resolution
- [Custom YAML loaders](examples/custom_loader.py) — extending the underlying PyYAML loader
- [Include limits](examples/include_limits.py) — controlling resource budgets

The [worked examples guide](docs/examples.md) walks through the corresponding configurations and results.
The [examples index](examples/README.md) links the runnable files to their expected-value tests.

## Development

Install development dependencies once:

```shell
python -m pip install -e ".[dev]"
```

Run the full CI check set locally:

```shell
python -m scripts.ci
```

GitHub Actions uses the same command coordinator: its test matrix runs
`python -m scripts.ci --tests-only`, and its quality job runs
`python -m scripts.ci --quality-only`. A local run covers both groups with the active
Python interpreter; CI also checks tests across Windows, Linux, and Python 3.10 through
3.15. Add `--fix` to apply safe Ruff, rumdl, and mdrepo fixes before checking.

The examples can also be run directly:

```shell
python examples/load_config.py
python examples/in_memory_config.py
python examples/custom_loader.py
python examples/include_limits.py
python -m pytest -v
```

The package supports Python 3.10 and newer.

Behavioral examples are exercised by [`tests/test_examples.py`](tests/test_examples.py),
with individual operator semantics covered by [`tests/test_semantics.py`](tests/test_semantics.py).

---

**YAML for the data. A few explicit operators for composing it.**
