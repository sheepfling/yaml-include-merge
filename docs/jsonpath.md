# JSONPath selection

`json_path` on an `!include` options mapping selects a list of values from a fully
constructed source. It is a
separate operation from JSON Pointer: its query is never appended to, parsed out of, or
percent-decoded as part of a filename. JSONPath is included in a normal installation:

```shell
python -m pip install yaml-compose
```

The package requires [python-jsonpath](https://jg-rp.github.io/python-jsonpath/) with its `strict`
extra and `JSONPathEnvironment(strict=True)`. The upstream engine implements
[RFC 9535](https://www.rfc-editor.org/rfc/rfc9535.html); yaml-compose delegates query parsing
and evaluation rather than maintaining a second parser. Strict mode disables the engine's
nonstandard query extensions. Its strict dependencies supply Unicode regular expressions
and I-Regexp checking. Queries do not execute Python code, and yaml-compose does not register
custom functions or callbacks. An incomplete installation fails explicitly; there is no silent fallback to a different
engine. JSON Pointer uses built-in exact traversal rather than this engine.
Alternative YAML parser backends are separate from selection and remain future optional
integrations; the supported default remains PyYAML.

## Syntax and results

```yaml
%LOADER YamlCompose
---
enabled: !include {path: catalog.json, json_path: '$.items[?@.enabled == true]'}
names: !include {path: catalog.json, json_path: '$.items[*].name'}
recent: !include {documents: all, path: events.yaml, json_path: '$[-3:]'}
steps: [start, !spread [!include {path: workflow.json, json_path: '$.steps[*]'}], finish]
```

`json_path` is supported only by `!include` and cannot be combined with `json_pointer`.
With `sources`, the query applies separately to each completed file value, retaining one
nested nodelist per file. Text and raw includes have line/byte-range options instead;
table includes do not accept JSON selectors. There is no inline query directive.
The query must be a literal YAML string, not another tag, list, or mapping. Quote queries
to avoid YAML punctuation and implicit-scalar rules. Each source is completely parsed
and its nested composition finished before selection; malformed unselected data still
fails. Include roots, search order, resolver identity, cycle checks, and file/count/byte
budgets are unchanged.

The result is always a list: zero matches give `[]`, one match gives `[value]`, and multiple
matches give `[value1, value2, ...]`. Missing members and out-of-range indexes follow JSONPath's
empty-match semantics rather than JSON Pointer's missing-path error. Duplicate matches remain
duplicates. A matched array is one value and stays nested unless the query selects its members.
The engine's RFC ordering applies; do not depend on object-member iteration order.

`$` selects the entire source as one result, including a string, number, boolean, or null.
A string is a scalar, not JSON text to parse again. `!include` with `documents: all` supplies
its explicit list of documents, so `$[1]` selects the second document. Queries do not change single-document
`!include` behavior or invent a multi-document stream interpretation.

That document-list root is retained for zero, one, or many YAML documents. `$[1:3]` selects
the second and third documents, `$[-2:]` the last two, and `$[::-1]` all documents in reverse
order. `$` matches the entire document list as one nested value; `$[:]` matches each document.
If a document is itself an array, it stays nested unless the query explicitly selects inside it.
For `!include` with `sources` and `documents: all`, each query sees one file's document list
and the outer result retains one nodelist per file, including empty lists. The same `sources` request does not select across
that outer file collection. To do so, put the collection in a separate aggregate document
and include that completed document with its own selector.

`!spread` explicitly inserts matched values into an outer list. A result list is not an
implicit mapping merge: `<<<: !include {path: data.json, json_path: '$'}` is an error
even with exactly one mapping match.
Use JSON Pointer when one known mapping is needed for a merge. Invalid query syntax, wrong
source types, missing dependencies, and exhausted limits are errors, never partial results.
Errors identify the query and YAML mark and preserve a directly included source's include route.

## Input domain

JSONPath operates on JSON-compatible values only: dictionaries with string keys, lists,
strings, booleans, integers, finite floats, and null. YAML dates, sets, binary values, non-string
mapping keys, non-finite numbers, tuples, and custom Python objects are rejected explicitly.
This is stricter than JSON Pointer over native included values. It avoids silently coercing
keys or changing YAML-only values to fit JSON semantics.

Cyclic YAML aliases are rejected. Repeated non-cyclic aliases are allowed but each expansion
counts toward the input limits. Evaluation uses a serialized snapshot; matches are returned
as ordinary JSON-compatible Python values without preserving source object or alias identity.
Raw bytes are outside this input domain. Text include mappings do not accept JSON selectors.

## Evaluation limits and process requirements

Each selection uses a fresh isolated Python subprocess. It runs the same interpreter with
`-I`: dependencies must be installed in that interpreter's environment, not supplied through
`PYTHONPATH`, the current directory, or user-site packages. Standard virtual environments are
supported. The runtime must permit launching a Python subprocess and reading the packaged
worker file. Embedded or frozen Python applications without that capability are not supported.
The subprocess provides a killable execution deadline, not a speed optimization. There is
startup and serialization overhead for every query; this is not a streaming reader.

Source-byte limits bound reading the original file. Separately, `JsonPathLimits` bounds the
parsed value presented to the query and its results: aliases can expand and a small query
can select large or repeated values. These are distinct budgets, not duplicate file limits.

The default immutable `JsonPathLimits` profile is:

| Field              | Default   | Bound                                                   |
| ------------------ | --------- | ------------------------------------------------------- |
| `max_query_bytes`  | 16,384    | UTF-8 query bytes                                       |
| `max_input_bytes`  | 8,388,608 | Serialized JSON document bytes                          |
| `max_input_nodes`  | 100,000   | Values visited, counting repeated alias expansions      |
| `max_input_depth`  | 128       | Nested input depth, with the root at depth zero         |
| `max_matches`      | 10,000    | Number of matched values                                |
| `max_output_bytes` | 8,388,608 | Serialized result list bytes                            |
| `timeout_seconds`  | 2.0       | Evaluation subprocess deadline, including engine import |

Pass a profile to `load_yaml_file`, `load_yaml_compose`, `load_yaml_dispatch`, or
`YamlConfigLoader` with `jsonpath_limits=`. Nested includes inherit the same profile. For example:

```python
from yaml_compose import JsonPathLimits, YamlConfigLoader

loader = YamlConfigLoader(
    jsonpath_limits=JsonPathLimits(max_matches=1000, timeout_seconds=5.0),
)
```

All bounds must be positive and finite; they cannot be disabled with `None`. Limits apply to
one selection, not cumulatively to all queries in a document. Input validation and serialization
run in the caller before the evaluation deadline starts. Operating-system process creation
itself may not be interruptible. An evaluation deadline kills and reaps the subprocess rather
than leaving a background query running. Output is collected only after successful evaluation;
errors do not return a truncated match list.

These are input/result bounds and a process deadline, **not a hard memory sandbox**. The engine
can allocate intermediate structures or perform expensive regular-expression work before
producing matches. The deadline limits how long that evaluation process can continue, but
cannot guarantee a peak-memory bound or make arbitrary untrusted workloads harmless. Existing
YAML parsing and caller-supplied constructors are also outside this evaluation deadline.
