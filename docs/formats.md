# Included document formats

`yaml-compose` uses YAML as its composition language. An including YAML document owns the
`!include`, merge, `!delete`, `!replace`, and `!spread` operations. An included non-YAML file
is parsed into its native Python value; the package does not interpret YAML tags inside it.

The default registry selects YAML for `.yaml` and `.yml`, strict JSON for `.json`, JSONC for
`.jsonc`, JSON5 for `.json5`, JSON Lines for `.jsonl`, NDJSON for `.ndjson`, CSV for `.csv`, TOML for
`.toml`, and INI for `.ini`. Dotenv is selected for `.dotenv` and the exact basename `.env`.
Basenames and suffixes match case-insensitively; exact basename aliases are checked first.
JSONC masks comments and then uses the same strict JSON parser as `.json`; duplicate
keys, trailing commas, and non-finite values remain errors. Comment masking preserves line
breaks and token boundaries. JSON5 is optional:
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

## JSON Lines and NDJSON records

Both record formats require UTF-8 without a BOM and parse one strict JSON value per LF-delimited
record. Objects, arrays, strings, numbers, booleans, and `null` stay as native Python values in
order. CRLF is accepted. The parser splits only on LF, so Unicode line separators inside JSON
strings are ordinary characters and a bare CR never acts as a record delimiter. Errors identify
the physical record line and include a parser column when one is available. Duplicate keys and
non-finite numbers are rejected with the same strict JSON rules used by `.json`.

An empty source produces `[]` as this package's zero-record result. A final LF does not create an
extra empty record. JSON Lines permits a missing final line terminator. NDJSON serialization
requires a final LF; this reader accepts a final record without one as an interoperability
extension. The [JSON Lines format documentation](https://jsonlines.org/) and the
[NDJSON specification](https://github.com/ndjson/ndjson-spec) define these format conventions.

JSON Lines rejects blank and whitespace-only records. NDJSON also rejects them by default, but
its specification permits parsers to ignore empty lines and recommends configurable behavior.
Opt in for a loader by replacing the built-in NDJSON adapter in that loader's registry:

```python
from yaml_compose import FormatRegistry, NdjsonAdapter, YamlConfigLoader

formats = FormatRegistry()
formats.register(NdjsonAdapter(ignore_blank_lines=True), replace=True)
loader = YamlConfigLoader(format_registry=formats)
```

The setting is immutable and local to the registry snapshot. When enabled, empty and
whitespace-only NDJSON records are skipped; JSON Lines remains strict.

The public `RecordFormatAdapter` protocol marks this capability separately from single-document
parsers. It exposes `iter_records(FormatInput)`, where the input already contains bounded bytes
and inert source identity. The include host closes the source stream before invoking the adapter,
then eagerly consumes and closes the iterator while the YAML include context is active. Includes
therefore return a buffered list: a late record error fails the whole include and no partial list
escapes. This is not constant-memory file streaming. YAML `!spread` can insert records into a
sequence. A record list is not a mapping, so it cannot be used as a `<<` or `<<<` merge source.

## CSV rows and header records

The built-in `.csv` adapter uses Python's standard-library `csv.reader` with its default
Excel-style comma and quoting rules, `strict=True`, and no dialect or header sniffing. This
uses the standard-library parser's rules; it is not a full RFC 4180 validator. For example,
a quote inside an unquoted field is accepted as a literal character, while an unclosed quoted
field or non-delimiter text after a closing quote raises a parse error. Input must be UTF-8
without a BOM. Every cell remains a string; values such as `001`, `true`, and `3.5` are not
converted.

The standard-library `csv.field_size_limit()` is an additional per-field parser limit. Its
value comes from the running Python runtime and is not fixed by `yaml-compose`. On the tested
CPython 3.10–3.14 runtimes, the reader applies it to decoded field characters (so a multibyte
UTF-8 character counts as one field character), independently of the include byte budgets.
The adapter does not read or change the process-wide setting; callers who change it affect
other CSV readers in that process too.

By default, CSV includes return a list of row lists and the first row is ordinary data:

```yaml
%LOADER YamlCompose
---
rows: !include people.csv
steps: [prepare, !spread [!include people.csv]]
```

To interpret the first row as headers and return dictionaries, replace the built-in adapter
in that loader's registry:

```python
from yaml_compose import CsvAdapter, FormatRegistry, YamlConfigLoader

formats = FormatRegistry()
formats.register(CsvAdapter(mode="dicts"), replace=True)
loader = YamlConfigLoader(format_registry=formats)
```

Header names and cell values are kept exactly as parsed. Header mode rejects empty or
duplicate header names and rejects each data row whose number of cells differs from the
header. Row-list mode keeps varying row widths. A physically blank line is an empty row in
row-list mode; in header mode it is an empty header or a ragged row. Empty input returns `[]`
in either mode, and a header-only file returns `[]` records in header mode. Parser errors
include the source and the physical line reported by the CSV reader. Both modes use the
shared include resolver, confinement, cycle checks, and byte/file/depth budgets. Results are
buffered lists, not streaming iterators.

The runnable [`include_csv.py`](../examples/include_csv.py) example loads the same YAML
include once in each mode; its exact result is checked by `tests/test_examples.py`.

## INI, dotenv, and text includes

The built-in `.ini` adapter uses `configparser` with interpolation disabled, strict duplicate
checking, case-preserving keys, and string values. It returns a mapping with a `DEFAULT` entry
when default values exist, followed by named sections. Each named section includes the values
inherited from `[DEFAULT]`, as `ConfigParser.items(section, raw=True)` does. Duplicate keys or
sections within one file and content outside a section are errors. Values remain text; the
adapter does not convert numbers, booleans, or `${...}` placeholders. `ConfigParser` removes
surrounding formatting whitespace around keys and values and supports its standard indented
multiline values. Whole-line `#` and `;` comments use the standard parser defaults; inline
comments are disabled, so those characters remain part of a value. See the
[Python `configparser` documentation](https://docs.python.org/3/library/configparser.html) for
the underlying INI syntax and parser behavior. `.conf` and `.cfg` are not guessed as INI. A
caller can explicitly add those aliases to its own registry:

```python
from yaml_compose import FormatRegistry, IniAdapter, YamlConfigLoader

formats = FormatRegistry()
formats.register(IniAdapter(), replace=True, suffixes=(".ini", ".conf", ".cfg"))
loader = YamlConfigLoader(format_registry=formats)
```

The default dotenv adapter accepts a documented line-oriented subset: blank and full-line
`#` comments, `KEY=value`, and optional `export KEY=value`. Keys follow shell environment
identifier spelling. Values are strings; surrounding single or double quotes are removed, while
their contents remain literal. A trailing `#` comment is accepted after a quoted value. In an
unquoted value, `#` is literal. `${NAME}`, backticks, command syntax, and backslashes are never
expanded or executed. Duplicate keys, malformed assignments, and unterminated quotes fail with
the physical line number. This is a parser only: it does not inspect or mutate `os.environ`.
The adapter does not claim full compatibility with every dotenv dialect. Both INI and dotenv
inputs require strict UTF-8 without a BOM; parser errors retain the include source context.

Use `!include-text` when a source should remain text rather than be parsed as a document:

```yaml
%LOADER YamlCompose
---
template: !include-text ./templates/service.md
```

It resolves and confines the path with the same resolver as `!include`, reads under the same
file/count/total-byte budgets, closes the source stream, and decodes strict UTF-8. It preserves
all decoded characters, including leading/trailing whitespace, CRLF, a final newline, and a
UTF-8 BOM character. It does not dispatch by suffix, parse YAML, apply directives, or execute
content. Use regular `!include` for typed YAML composition. Raw bytes are not exposed by this
tag.

[`include_non_yaml.py`](../examples/include_non_yaml.py) exercises the default INI, dotenv, and
text paths and explicitly opts into `.conf`. Exact results and failure cases are covered by
[`tests/test_ini_dotenv_formats.py`](../tests/test_ini_dotenv_formats.py) and
[`tests/test_include_text.py`](../tests/test_include_text.py).

## Register a parser

An adapter describes its stable format ID and suffixes, then parses one complete input. Exact
filenames can be declared for names such as `.env`; callers may also replace an adapter's suffix
and filename aliases when registering it:

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
- A record adapter implements `RecordFormatAdapter`, and its descriptor sets
  `capability="records"`; it supplies `iter_records(FormatInput) -> Iterator[object]` instead
  of `parse`. The registry checks that the declared capability has the matching method.
- The host resolves and confines sources, checks cycles and include budgets, reads bounded
  bytes, and closes the stream before calling an adapter. `FormatInput` exposes those bytes,
  a canonical source identity, and a display name. It does not expose a file opener.
- Adapter instances are shared by loader snapshots. Implementations must be stateless and
  safe for concurrent calls; keep parse state local. Adapters are trusted Python code, not
  sandboxed plugins. Input byte limits do not bound parser CPU or memory use.
- A registry is mutable while being configured. Each loader captures a read-only snapshot,
  so later registry changes affect new loaders only. Registering a duplicate ID or suffix
  fails atomically. `replace=True` can replace an adapter with the same ID; it cannot take a
  suffix or exact filename owned by another ID. The `suffixes=` and `filenames=` registration
  arguments replace that adapter's aliases in the new registration; use them to opt into
  ambiguous extensions such as `.conf`/`.cfg`.
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

Serializers and live file-stream APIs are not part of this adapter boundary. Record adapters
iterate over already-owned bounded bytes; include loading still returns a complete Python list.
