# Included document formats

`yaml-compose` uses YAML as its composition language. An including YAML document owns the
`!include`, `!include-table`, `!include-text`, `!include-raw`, merge, `!delete`, `!replace`,
`!jsonpath`, and `!spread` operations. An included non-YAML document is parsed into its native Python value;
the package does not interpret YAML tags inside it.

The default registry selects YAML for `.yaml` and `.yml`, strict JSON for `.json`, JSONC for
`.jsonc`, JSON5 for `.json5`, JSON Lines for `.jsonl`, NDJSON for `.ndjson`, TOML for `.toml`, and
INI for `.ini`. The registry also selects the CSV adapter for `.csv`, which is used only by
`!include-table`. Dotenv is selected for `.dotenv` and the exact basename `.env`.
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

## Include options and selection

Every single-source include tag accepts either a scalar literal path or an options mapping with a required
`path` string. The scalar form is unchanged: `!include "config#blue%20.yaml"` opens that exact
local name. It does not split `#`, percent-decode names, or interpret URI references. The
mapping form keeps the path and any selector separate:

```yaml
%LOADER YamlCompose
---
<<<: !include {path: defaults.yaml, json_pointer: /service}
array: !include {path: hardware.yaml, json_pointer: /arrays/kbr}
first_record: !include {path: records.jsonl, json_pointer: /0}
first_name: !include-table {path: people.csv, json_pointer: /1/0}
steps: [prepare, !spread [!include {path: workflow.yaml, json_pointer: /steps}], finish]
enabled: !include {path: catalog.json, json_path: '$.items[?@.enabled == true]'}
```

The supported fields are deliberately specific to each tag:

| Tag              | Fields beyond required `path`               |
| ---------------- | ------------------------------------------- |
| `!include`       | `documents`, `json_pointer` or `json_path`  |
| `!include-table` | `json_pointer` or `json_path`               |
| `!include-text`  | `encoding`, `lines: {start: ..., end: ...}` |
| `!include-raw`   | `offset`, `length`                          |

Options must be literal values of the documented types. Unknown fields, duplicate fields,
wrong types, and both JSON selectors together are errors before opening the source. Include
specifications do not evaluate custom tags or YAML merge expressions. Booleans are not
accepted as integer offsets or line numbers. `path` must be a nonempty string in a mapping;
`{path: filename}` is equivalent to the existing scalar path form under default construction.
A trusted custom loader's `construct_scalar` override still applies to scalar references;
mapping fields remain inert literals.

The local path uses the existing resolver and host-platform path policy. Windows fully
qualified drive and UNC paths remain subject to `allow_absolute_includes` and allowed roots;
drive-relative paths are rejected. Bare scheme-like names such as `foo:bar.yaml` are rejected
by existing path validation; a POSIX relative spelling such as `./foo:bar.yaml` is unambiguous.
No URL loading, path decoding, filesystem-existence guessing, or new network behavior is added.
Quote paths with YAML-significant characters; on Windows, single-quoted YAML strings preserve
backslashes literally, for example `path: 'C:\configs\config#blue.yaml'`.

### Select part of an include

`json_pointer` uses the plain string form of [JSON Pointer (RFC 6901)](https://www.rfc-editor.org/rfc/rfc6901).
It selects from the completed included value before that value is inserted, merged, or spread
into its parent document:

- `""` selects the entire value, including a scalar or `null`. Omitting a selector also includes
  the whole value. `/` selects the mapping key that is the empty string.
- `/arrays/kbr` follows exact string mapping keys. Keys are not coerced: a YAML integer key
  `1` is different from the string key `"1"`.
- `/items/0` selects the first sequence element. Indexes are zero-based ASCII decimal integers,
  without leading zeroes except `0` itself. Negative indexes, `-`, slices, wildcards, filters,
  and out-of-range indexes are errors.
- Escape a literal `/` in a key as `~1` and a literal `~` as `~0`. For example, `/a~1b/m~0n`
  selects key `m~n` inside key `a/b`. Invalid `~` escapes are rejected.
- This is not a URI fragment. Do not add a leading `#`. Spaces, Unicode, `#`, and percent signs
  are literal characters in the pointer string. `/space%20key` selects the key `space%20key`,
  while `/space key` selects `space key`. `/☃` selects `☃` without percent encoding.

JSONL and NDJSON are lists of parsed records: `/2/name` selects the `name` member in the third
record. CSV tables are lists of row lists: `/1/0` selects the first cell of the second row,
with the first row still ordinary data. Cells remain strings. A selected row remains a list;
a selected cell is a scalar. Custom mapping values and list/tuple sequences follow the same
rules. Other custom Python objects have no implicit attribute lookup. YAML aliases are followed
through the constructed value; evaluation takes one step per pointer token, so a cyclic alias
does not cause an unbounded walk. Existing restrictions on cyclic mapping overlays still apply.

The selected value must satisfy the operation that consumes it. `<<<` and `<<` require mapping
sources, and `!spread` requires a sequence. Selecting a scalar does not convert it to a mapping
or sequence. Errors identify the include path, selector, and include-site mark; nested errors
preserve the include route.

The complete source is read, parsed, and validated before selection. Nested YAML includes and
composition finish first. Unselected malformed data and nested include cycles still fail. All
source bytes and nested reads count toward the existing limits, even when the result is small.
Repeated includes count as separate reads as before. Resolvers receive only the literal path;
their returned source identity still controls format dispatch, confinement, and cycle checks.

Use the separate `json_path` option for explicit JSONPath queries. It has the same optional
strict engine, always-list result, JSON-compatible input requirements, and per-selection limits
as `!jsonpath [source, query]`. These selectors are mutually exclusive in one include mapping.
See [JSONPath selection](jsonpath.md) for filters, slices, functions, and limits.

### YAML document streams

Use the object-only `documents: all` option on `!include` to read a YAML stream as a list of
all its documents:

```yaml
%LOADER YamlCompose
---
documents: !include {path: bundle.yaml, documents: all}
settings: !include {documents: all, path: bundle.yaml, json_pointer: /1/settings}
recent: !include {documents: all, path: bundle.yaml, json_path: '$[-3:]'}
steps: [prepare, !spread [!include {path: steps.yaml, documents: all}], finish]
```

Without a selector, the result is always a list. An empty or comment-only stream gives `[]`;
a single mapping gives `[mapping]`; an explicit empty document gives `[null]`. Multiple `---`
documents produce one list item per document, in order. A document that is itself a sequence
stays nested: two sequence documents become `[sequence1, sequence2]`, never one flattened list.
Empty documents are retained and count when indexing.

The outer document list is deliberate, not a wrapper to discard for zero or one document.
It makes whole-stream JSONPath slices possible: `$[1:3]` selects the second and third documents,
`$[-2:]` selects the last two, and `$[::-1]` reverses document order. A sequence-valued document
remains one item; `$[1:3]` does not slice the contents of those documents. `$` selects the whole
document list as one match, whereas `$[:]` selects its individual documents.

A selector applies once to this document list after the complete stream is parsed. JSON Pointer
`/0` selects the first document, `/1/settings` selects `settings` in the second document, and
`""` retains the whole list. Even if a selector chooses an early document, a syntax error,
duplicate key, undefined alias, or failed include in a later document fails the whole include.
The parser is eager: no lazy iterator or partially validated result escapes.

Each document gets normal YAML composition and nested include behavior, with paths relative
to the included stream's source. Anchors and aliases are local to each document: names may be
reused in a later document, but an alias cannot reference an earlier document's anchor. The
leading `%LOADER YamlCompose` header is optional in an included stream. With
`recursive_dispatch=False`, all documents instead use the configured ordinary YAML loader and
do not enable nested package tags. Custom constructors and duplicate-key checks apply in either mode.

All documents share existing source and resource controls. The stream is one file read, its
entire byte size counts against file/total-byte limits, and every nested include uses the same
depth/count/byte budget. Cycle detection uses canonical source identity, not the document index
or selector. There is no separate document-count limit. As with ordinary YAML, byte limits do
not bound all parser CPU or expanded object memory.

The resolver's selected format must be YAML. Registered non-YAML formats produce an explicit
error with `documents: all`; unknown suffixes retain the YAML fallback. Resolver-provided
`format_id="yaml"` works. JSONL and NDJSON already return record lists through `!include`.
Scalar `!include` and mappings without `documents: all` remain single-document and reject multiple
documents, even with a selector. `documents: single` explicitly requests the same default behavior.
In single-document mode, `/0` selects a sequence element, not a stream document. This document-list model is explicit
yaml-compose behavior, not the URI-fragment syntax defined by RFC 9512. Named-anchor fragments
such as `#*name` are not supported.

A pointer-selected mapping can be a `<<` or `<<<` source. An unselected document list or JSONPath
result list cannot: there is no implicit stream merge. Select a mapping with JSON Pointer, or
use an explicit YAML list of separately selected mapping sources. Existing merge precedence is
unchanged. `!spread` inserts documents into an outer sequence, not the contents of sequence-valued documents.

### Text lines and encoding

`!include-text` defaults to strict UTF-8 decoding and returns the exact decoded string. Its
mapping form can select a text encoding and an inclusive range of physical lines:

```yaml
notes: !include-text {path: notes.txt, lines: {start: 2, end: 4}}
legacy: !include-text {path: legacy.txt, encoding: latin-1}
tail: !include-text {path: notes.txt, lines: {start: 5}}
```

`encoding` must be a string naming a supported Python text codec. Decoding is strict: malformed
input is an error, not replacement text. The complete source is decoded before line selection,
so an encoding error in an unselected line still fails. No newline conversion is applied.

`lines.start` defaults to 1; `lines.end` is optional. Both are positive integers, one-based and
inclusive. An end before the start is an error. CR, LF, and CRLF delimit physical lines;
original terminators are retained. A trailing terminator does not create another empty line.
The end is clipped at EOF, and a start beyond EOF gives an empty string. Omitting `lines`
returns the whole decoded text. `json_pointer` and `json_path` are not text-include options.

### Raw byte ranges

`!include-raw` returns exact bytes. Its mapping form can select a byte range:

```yaml
header: !include-raw {path: payload.bin, offset: 0, length: 16}
remainder: !include-raw {path: payload.bin, offset: 16}
```

`offset` is zero-based and defaults to 0. `length` is optional; without it, all bytes from the
offset to EOF are returned. Both are nonnegative integers. Zero length or an offset beyond EOF
gives empty bytes; the end is clipped at EOF. No decoding occurs. JSON selectors, encodings,
and line ranges are not raw-include options.

Text and raw ranges are selections after a complete bounded read, not partial-file IO. The
entire source counts toward file and total-byte limits, even for an empty selected range.
All resolver/root/count/depth policies and stream cleanup still apply.

## Collect several structured files

`!include-many` is an object-only operation for explicitly ordered paths and filesystem globs:

```yaml
%LOADER YamlCompose
---
configs: !include-many
  paths:
    - base.yaml
    - {path: 'special#;%20.yaml'}
    - {glob: 'profiles/*.yaml'}
  json_pointer: /service
```

Unlike the single-source include tags, it requires a mapping with `paths`, a sequence of
entries. Each entry is either a literal path string, `{path: literal_path}`, or an explicit
`{glob: pattern}`. A string containing `*`, `?`, `[`, `#`, or `%` stays a literal path; only a
`glob` entry expands patterns. Names are never interpreted as URI references or percent-decoded.
There is no scalar `!include-many` shorthand and no implicit comma/semicolon-delimited path list.
Empty `paths: []` returns `[]`.

Optional top-level fields are `documents: single` (the default) or `documents: all`, and either
`json_pointer` or `json_path`. Entries do not accept their own selectors or include-option
objects. Unknown/duplicate fields, non-string paths, invalid document modes, YAML merge
expressions, and tagged expressions in options are errors before source resolution.

### Result shape and selection

The output has one item per resolved file, in source order. `documents: single` loads each
file as ordinary `!include`; `documents: all` loads each file as `!include {path: ..., documents: all}`, which requires
YAML. CSV tables and raw/text-specific reads are not added to ordinary structured include
semantics by using this tag. A source array stays one nested array, a YAML stream stays one
nested document list, and a JSONPath selection stays one nested nodelist. Nothing is flattened.

Selection is applied independently to each **completed file result**, not to the outer result
list and not separately to each document in `documents: all` mode:

```yaml
second_document_settings: !include-many
  paths: [one.yaml, two.yaml]
  documents: all
  json_pointer: /1/settings

all_names: !include-many
  paths: [{glob: 'records/*.json'}]
  json_path: '$.items[*].name'

recent_documents_per_file: !include-many
  paths: [one.yaml, two.yaml]
  documents: all
  json_path: '$[-2:]'
```

For the first example, `/1/settings` selects from the document list for each file. If either
file lacks that path, the whole operation fails. JSONPath's zero/one/many matches remain a
list for each file, including `[]` for a file with no matches. Both selectors retain their
single-source validation rules and JSONPath's optional dependency and resource controls.

The last example returns one list of up to two documents per file, not the last two files.
To query the completed file collection instead, apply a separate `!jsonpath` around
`!include-many`. Per-document selector scope is not implicit: the document list remains
the selection root in `documents: all` mode.

A failure in any source fails the operation; no partial list escapes. Each source's full
content is parsed before selection, including later YAML documents and nested includes.
Nested paths resolve relative to that source's containing directory as usual. The aggregate
list cannot be a `<<` or `<<<` mapping source. Use `!spread` if you explicitly want to insert
its per-file results into another sequence; this still does not flatten an individual result.

### Explicit glob semantics

Globs use path-segment patterns: `*`, `?`, and bracket character classes. A complete `**`
segment matches zero or more directories. Hidden names are included when the pattern matches
them. Brace expansion, tilde expansion, and shell execution are not supported. An exact
pattern such as `{glob: config.yaml}` is valid and matches zero or one file.

Path entries retain their listed order. Each glob's matches are sorted by normalized path
spelling in case-sensitive Unicode lexical order. Pattern matching itself follows native
platform case normalization. Each spelling appears once within a single glob, including a
recursive pattern with multiple ways to reach it. Repeating entries or using overlapping
globs retains duplicate reads and duplicate results. Distinct symlink spellings are not
collapsed merely because they resolve to the same file. A glob with no matching files is
an error rather than an empty contribution.

Bare patterns follow `include_search`. Each uses the first search location that produces
matching files; results from different locations are not combined. Explicit `./` or `../`
patterns require a containing filesystem source and do not fall back to search paths.
Absolute patterns obey `allow_absolute_includes`. There is no working-directory fallback.
As with ordinary paths, host-native separators apply; Windows drive/UNC spellings do not
turn into Windows filesystem access when running on POSIX.

Globbing is supported only with the built-in local filesystem resolver. Literal entries work
with custom resolvers, but a glob does not silently fall back from a custom resolver to the
filesystem. Globs do not fetch URLs or add any network-resolution capability.

### Glob confinement and budgets

The fixed non-wildcard directory prefix is canonicalized and must lie inside an allowed root
before scanning starts. A broad pattern cannot start outside allowed roots and scan toward
an allowed descendant. `..` in a fixed prefix is permitted only when the resulting prefix
is confined; `..` after the first wildcard segment is rejected.

Wildcard directory traversal does not follow directory symlinks. An explicitly named fixed
symlink prefix may be used only when its canonical target is inside the allowed roots. A
matched symlink file outside those roots is an error. Directory results are not included as
file values. These rules avoid treating pattern expansion as a way around ordinary include
confinement; they do not make concurrent filesystem mutations race-free.

Every selected file, including duplicate reads, consumes the normal include-count and byte
budgets. Nested includes share those budgets and retain depth/cycle checks. In addition,
`IncludeLimits.max_glob_steps` defaults to `100_000` and bounds location setup, directory/pattern states, and
directory entries examined across all globs in a top-level load, including nested expansions.
An empty directory still consumes traversal work. Set the profile field to `0` to forbid glob
work or `None` to leave traversal unbounded. Literal entries do not consume glob steps.
This field is configured through `include_limits`, not a new per-call `max_glob_steps` argument:

```python
from yaml_compose import IncludeLimits, YamlConfigLoader

loader = YamlConfigLoader(include_limits=IncludeLimits(max_glob_steps=10_000))
```

The traversal budget bounds expansion work, not elapsed time, file mutation, or total Python
memory. Resource-limit failures are errors and never return a truncated list of matches.

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

## CSV tables

The built-in `.csv` adapter is invoked with `!include-table`, not `!include`. Using `.csv` with
ordinary `!include` is a marked error explaining the table tag requirement; the input is never
silently interpreted as YAML. Other table-shaped sources, such as TSV, are not guessed or
enabled by default. A caller can register a table adapter for a chosen suffix and use the same
`!include-table` tag; this keeps the YAML syntax clear while letting the adapter define its
delimiter and parsing rules. The built-in adapter uses Python's standard-library `csv.reader` with its default
Excel-style comma and quoting rules, `strict=True`, and no dialect or header sniffing. This
uses the standard-library parser's rules; it is not a full RFC 4180 validator. For example,
a quote inside an unquoted field is accepted as a literal character, while an unclosed quoted
field or non-delimiter text after a closing quote raises a parse error. Input must be UTF-8
without a BOM. Every cell remains a string; values such as `001`, `true`, and `3.5` are not
converted.

CSV tables return a list of row lists; the first row is ordinary data and each cell remains
a string:

```yaml
%LOADER YamlCompose
---
rows: !include-table people.csv
steps: [prepare, !spread [!include-table people.csv]]
```

The Python CSV reader also applies its process-wide `csv.field_size_limit()` to each decoded
field, separately from include byte budgets. `yaml-compose` does not change that setting.

The include uses the ordinary filesystem resolver unless the caller supplies a custom
resolver. It remains subject to the same root confinement and byte, file-count, and depth
budgets as other includes. Results are buffered lists, not streaming iterators. Empty input
returns `[]`; blank lines become empty rows, and rows may have different widths. Parsing
errors include the source and physical line reported by the CSV reader.

CSV dispatch through `!include` was part of an unpublished local prototype. This candidate
requires `!include-table` so the table-shaped interpretation is visible in YAML.

The runnable [`include_csv.py`](../examples/include_csv.py) example shows the complete basic
workflow, and its exact result is checked by `tests/test_examples.py`.

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

Use `!include-text` when a source should remain decoded text rather than be parsed as a document:

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

Use `!include-raw` when a source must remain exact bytes:

For a normal local file, write its path directly; no custom resolver or loader setup is needed:

```yaml
%LOADER YamlCompose
---
payload: !include-raw ./assets/payload.bin
```

The default filesystem resolver opens the source in binary mode. The tag returns the original
bytes without decoding, parsing, or executing them; NUL and non-UTF-8 bytes are preserved. Path
resolution, root confinement, stream ownership, include depth/count, and byte limits are the same
as for `!include` and `!include-text`. If you provide a custom resolver, it must return a binary
stream; a text stream is rejected rather than encoded. Python `bytes` values are not
JSON-serializable without an explicit application conversion. This default filesystem path is
covered by [`test_include_raw_preserves_every_byte_and_empty_sources`](../tests/test_include_raw.py).

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

An additional table adapter uses the same record iterator contract but declares
`capability="table"`. Registering a TSV parser does not change the built-in `.csv` parser:

```python
import csv
from collections.abc import Iterator
from io import StringIO

from yaml_compose import FormatDescriptor, FormatInput, FormatRegistry, YamlConfigLoader


class TsvAdapter:
    descriptor = FormatDescriptor("tsv", (".tsv",), capability="table")

    def iter_records(self, source: FormatInput, /) -> Iterator[object]:
        text = source.data.decode("utf-8")
        with StringIO(text, newline="") as stream:
            yield from csv.reader(stream, delimiter="\t", strict=True)


formats = FormatRegistry()
formats.register(TsvAdapter())
loader = YamlConfigLoader(format_registry=formats)
```

Then `rows: !include-table people.tsv` selects that registered parser. Ordinary `!include`
rejects table-capability formats.

The example parser is intentionally small; production adapters should report expected
input failures by raising `FormatParseError` with a useful message. The loader attaches the
YAML include mark and preserves the exception chain.

## Adapter and registry contract

- An adapter implements the `FormatAdapter` protocol: a class-level `FormatDescriptor` and
  `parse(FormatInput) -> object`.
- A record adapter implements `RecordFormatAdapter`, and its descriptor sets
  `capability="records"`; it supplies `iter_records(FormatInput) -> Iterator[object]` instead
  of `parse`. The registry checks that the declared capability has the matching method.
- A table adapter uses the same bounded record iterator with `capability="table"`. It is
  selected only by `!include-table`, keeping table parsing explicit and separate from ordinary
  structured includes.
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

The public `UNSET` object and `UnsetType` exist for advanced wrappers that must preserve the
difference between an omitted option and an explicit `None`. Ordinary callers should omit the
argument or pass its documented value; they do not need to import either name.

The default `YamlType` does not include tuples. Built-in YAML loading with `SafeLoader` creates
YAML sequences as lists. A custom constructor or format adapter can return tuples and other
Python containers, so calls with custom loaders or registries are typed as `object`.

Serializers and live file-stream APIs are not part of this adapter boundary. Record adapters
iterate over already-owned bounded bytes; include loading still returns a complete Python list.
