# Changelog

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
