# Changelog

## Unreleased

- Add object-only `!include-many` for ordered literal paths and explicit filesystem globs,
  preserving one result per file and applying document mode and selection independently to
  each completed source. Keep duplicates across entries, reject no-match globs, confine
  traversal to allowed roots, and share a configurable `max_glob_steps` expansion budget.
- Add options mappings across all include tags while preserving every scalar path literally,
  including filenames containing `#` and `%`. Structured includes support mutually exclusive
  plain `json_pointer` and `json_path` fields, separate from the source path.
- Add text encoding and inclusive physical-line selection to `!include-text`, and offset/length
  byte selection to `!include-raw`. Validate options before opening and charge complete reads
  against existing budgets before selecting any range.
- Add optional explicit JSONPath selection through `json_path` or `!jsonpath [source, query]`,
  with a strict RFC 9535 engine, always-list results, JSON-domain validation, configurable
  input/result bounds, and isolated subprocess deadlines.
- Add explicit `!include-all` for YAML document streams, always returning a document list
  before optional selection. Keep ordinary `!include` single-document semantics, document-local
  anchors, complete-stream validation, and shared include budgets.
- Add plain JSON Pointer selection across structured mapping members, sequence elements,
  JSONL/NDJSON records, and CSV rows/cells. Preserve full-source validation, include routes,
  resolver confinement, and resource budgets before selection.

## 0.1.1

- Extend includes with strict JSON, JSONC, optional JSON5, JSONL/NDJSON, TOML, INI,
  dotenv, and CSV table inputs. The per-loader format registry also supports custom
  adapters, while YAML remains the composition language.
- Add `!include-table` for CSV rows of strings, `!include-text` for UTF-8 text, and
  `!include-raw` for exact bytes.
- Preserve nested include routes in errors and support Python 3.10 through 3.15.

## 0.1.0

Initial release of `yaml-compose`, a PyYAML adapter for composing configuration files.

- Safe YAML loading by default, with duplicate explicit mapping keys rejected and
  application-selected PyYAML loaders supported.
- File includes with nested relative resolution, allowed roots, cycle checks, configurable
  search precedence, and injectable filesystem or in-memory resolvers.
- Shallow `<<` merges and recursive `<<<` overlays. Local keys win; earlier sources in
  a merge sequence take precedence over later sources.
- `!delete` removes inherited keys, `!replace` replaces a mapping, and `!spread` inserts
  anchored or included sequence items at their declared position.
- In-memory mapping overlays and list spreads work without search paths or allowed roots;
  `!include` checks for an allowed root when it is used.
- Reusable include-limit profiles, configurable library defaults, per-load overrides,
  and `None` for individual unbounded resources. Profiles can be adjusted for later loads;
  each load captures its own limits snapshot. Default budgets allow 32 include levels,
  1,024 reads, 8 MiB per file, and 64 MiB total.
- Typed public APIs, runnable examples, and tests on Python 3.10–3.15 on Linux and Windows.
- MIT licensing, credited to `sheepfling`.
