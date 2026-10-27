# Runnable examples

The top-level [README](../README.md) introduces the composition semantics. This index
links the runnable example files to the tests that check their results. Run
`python examples/load_config.py` to print the quickstart values as JSON.

## Simple README examples

- [quickstart/production.yaml](quickstart/production.yaml) combines deep
  overrides and file spreads using [defaults](quickstart/base.yaml) and
  [steps](quickstart/extra_steps.yaml).
- [include_value.yaml](include_value.yaml) loads
  [database.yaml](database.yaml) as one mapping value.
- [include_formats.yaml](include_formats.yaml) combines a JSON mapping,
  [TOML values](include_formats/database.toml), and a JSON list spread. The
  [worked guide](../docs/examples.md#include-json-and-toml-sources) shows the
  exact resolved result and Python date-time types.
- [include_records.yaml](include_records.yaml) includes a JSON Lines list and
  spreads NDJSON records; the [worked guide](../docs/examples.md#json-lines-and-ndjson-records)
  documents buffered record semantics.
- [include_csv.py](include_csv.py) loads CSV once as row lists and once as
  header-keyed dictionaries; the [worked guide](../docs/examples.md#csv-rows-and-header-records)
  describes the explicit modes.
- [include_non_yaml.py](include_non_yaml.py) includes INI, dotenv, and literal
  text through YAML. It also opts into `.conf` parsing explicitly; the
  [Markdown payload](include_non_yaml/notes.md) is included as raw text. The
  [worked guide](../docs/examples.md#ini-dotenv-and-text-includes) documents the
  returned shapes and safety boundaries.
- [shallow_merge.yaml](shallow_merge.yaml) replaces a nested mapping with `<<`.
- [deep_merge.yaml](deep_merge.yaml) preserves nested defaults with `<<<`.
- [merge_precedence.yaml](merge_precedence.yaml) shows earlier source priority
  and local overrides written before the merge key.
- [spread_sources.yaml](spread_sources.yaml) spreads an anchor and
  [file items](extra-items.yaml) at their positions.
- [replace_mapping.yaml](replace_mapping.yaml) replaces one inherited mapping
  with `!replace`.

The shallow, deep, and replacement examples share
[merge_defaults.yaml](merge_defaults.yaml). Explicit `./` paths pin their
file references to the containing example's directory.

[`tests/test_examples.py`](../tests/test_examples.py) asserts the exact outputs
of these files and the [runner](load_config.py). Update the example files and
expected values together when an example changes.

## Further examples

- [base.yaml](base.yaml), [region.yaml](region.yaml), and
  [production.yaml](production.yaml) show three layers, list replacement,
  an empty list, `null`, type changes, and nested deletion.
- [merge_modes.yaml](merge_modes.yaml) compares anchor and file sources,
  source priority, deletion, and inline, anchor, file, and empty mapping
  replacement. It uses [defaults](merge_defaults.yaml), a
  [lower-priority source](merge_later.yaml), and a
  [replacement mapping](merge_replacement.yaml).
- [include_search.py](include_search.py) compares all four lookup policies.
  Its [nested configuration](search/app/main.yaml) combines deep overrides
  and spreads, with an explicit relative file that stays local in every mode.
  The [worked guide](../docs/examples.md#configurable-include-lookup) explains
  the local and shared source files and their expected values.
- [in_memory_config.py](in_memory_config.py) uses an application-owned namespace.
- [custom_loader.py](custom_loader.py) passes a selected PyYAML loader into
  nested includes.
- [include_limits.py](include_limits.py) disables the file-count bound for one load.

The [worked examples guide](../docs/examples.md) develops these cases further.
[`tests/test_semantics.py`](../tests/test_semantics.py) isolates the operators,
while [`tests/test_include_search.py`](../tests/test_include_search.py) and
[`tests/test_loader.py`](../tests/test_loader.py) check lookup and invalid inputs.
[`tests/test_composition.py`](../tests/test_composition.py) combines nested
includes, lookup policies, deep overlays, and list spreads on filesystem and
memory sources. It also checks shared budgets, source locations, and errors
that cannot be hidden by later overrides.

## Run

From the repository root after installing the package:

```shell
python examples/load_config.py
python examples/include_formats.py
python examples/include_records.py
python examples/include_csv.py
python examples/include_non_yaml.py
python examples/include_search.py
python examples/in_memory_config.py
python examples/custom_loader.py
python examples/include_limits.py
python -m pytest tests/test_examples.py tests/test_semantics.py tests/test_include_search.py
```
