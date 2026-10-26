# Included document formats

`yaml-compose` uses YAML as its composition language. An including YAML document owns the
`!include`, merge, `!delete`, `!replace`, and `!spread` operations. An included non-YAML file
is parsed into its native Python value; the package does not interpret YAML tags inside it.

The default registry selects YAML for `.yaml` and `.yml`, strict JSON for `.json`, JSONC for
`.jsonc`, JSON5 for `.json5`, and TOML for `.toml`. JSONC masks comments and then uses the
same strict JSON parser as `.json`; duplicate keys, trailing commas, and non-finite values
remain errors. Comment masking preserves line breaks and token boundaries. JSON5 is optional:
install `yaml-compose[json5]` to enable it. Its parser loads lazily only when a `.json5` source
is selected. TOML uses `tomllib` where available and the package's existing `tomli`
dependency on Python 3.10. Unknown suffixes continue through YAML for compatibility.
Selection uses the resolved source suffix; a resolver can instead set
`ResolvedInclude.format_id` to select a registered adapter explicitly. An unknown explicit ID
is an error. No content sniffing or entry-point discovery occurs.

JSON, JSONC, JSON5, and TOML sources must be UTF-8 without a byte-order mark (BOM). JSONC
comment replacement preserves source coordinates; malformed strict JSON errors therefore
keep their original line and column. Unterminated block comments report their opening
coordinate. JSON5 syntax errors are `ValueError`s from the parser with line and column text;
duplicate-key errors have no token coordinate, so the loader does not invent one. JSON5
`NaN`, `Infinity`, and decimal values that overflow Python floats retain the parser's native
float result (`nan` or positive/negative infinity). The JSON5 dependency is pure Python and
has no runtime dependencies; its maintainer warns that it is substantially slower than the
standard JSON parser and deeply nested input may reach Python's recursion limit. See the
[json5 0.17.3 package page](https://pypi.org/project/json5/0.17.3/) and its
[maintainer documentation](https://github.com/dpranke/pyjson5/blob/main/README.md).

## Register a parser

An adapter describes its stable format ID and suffixes, then parses one complete input:

```python
from yaml_compose import FormatDescriptor, FormatInput, FormatRegistry, YamlConfigLoader


class KeyValueAdapter:
    descriptor = FormatDescriptor("key-value", (".kv",))

    def parse(self, source: FormatInput, /) -> object:
        values: dict[str, str] = {}
        for line in source.data.decode("utf-8").splitlines():
            if line.strip():
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
        return values


formats = FormatRegistry()
formats.register(KeyValueAdapter())
loader = YamlConfigLoader(format_registry=formats)
config = loader.load_file("app.yaml")
```

Given `app.yaml`:

```yaml
%LOADER YamlCompose
---
<<<: !include service.kv
service:
  port: 8080
```

and `service.kv` containing `host = localhost`, the included mapping is the underlay and
the YAML values take precedence. The result is:

```python
{"host": "localhost", "service": {"port": 8080}}
```

The example parser is intentionally small; production adapters should report expected
input failures by raising `FormatParseError` with a useful message. The loader attaches the
YAML include mark and preserves the exception chain.

## Adapter and registry contract

- An adapter implements the `FormatAdapter` protocol: a class-level `FormatDescriptor` and
  `parse(FormatInput) -> object`.
- The host resolves and confines sources, checks cycles and include budgets, reads bounded
  bytes, and closes the stream before calling an adapter. `FormatInput` exposes those bytes,
  a canonical source identity, and a display name. It does not expose a file opener.
- Adapter instances are shared by loader snapshots. Implementations must be stateless and
  safe for concurrent calls; keep parse state local. Adapters are trusted Python code, not
  sandboxed plugins. Input byte limits do not bound parser CPU or memory use.
- A registry is mutable while being configured. Each loader captures a read-only snapshot,
  so later registry changes affect new loaders only. Registering a duplicate ID or suffix
  fails atomically. `replace=True` can replace an adapter with the same ID; it cannot take a
  suffix owned by another ID.
- The built-in YAML entry stays private to the loader so PyYAML nodes, constructors, and
  source marks retain their current behavior. A `FormatSelection` for YAML has no public
  adapter. JSONC uses strict JSON values, JSON5 keeps native parser values including special
  floats, and TOML keeps its date and time objects.
- Calls with the default loader and built-in registry return the `YamlType` alias. It covers
  standard safe YAML values plus the native JSON/TOML results. Supplying a custom registry
  or YAML loader returns `object`, because an application adapter or constructor can return
  arbitrary Python values.
- `YamlConfigLoader` defaults its type parameter to `YamlType`, so existing bare class
  annotations continue to mean the built-in result type. Custom parser or YAML loader
  configuration infers `YamlConfigLoader[object]`; explicit annotations can use either
  type argument.
- Passing `yaml_loader=SafeLoader` explicitly still returns `object`. The accepted
  parameter type also permits SafeLoader subclasses with arbitrary constructors, so an
  overload returning `YamlType` would be unsound. Omit the redundant argument to keep the
  default result type; validate custom-loader results before narrowing them in application
  code.

This phase covers complete single-document parsers. It does not define record-stream or
serializer APIs. Applications that need a JSON Lines-like format should wait for that
separate capability instead of treating a stream as an ordinary single document.
