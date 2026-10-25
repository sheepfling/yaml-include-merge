# Changelog

## 0.1.0 (unreleased)

Initial release of `yaml-compose`, a PyYAML adapter for composing configuration files.

- Safe YAML loading by default, with duplicate explicit mapping keys rejected and
  application-selected PyYAML loaders supported.
- File includes with nested relative resolution, allowed roots, cycle checks, configurable
  search precedence, and injectable filesystem or in-memory resolvers.
- Shallow `<<` merges and recursive `<<<` overlays. Local keys win; earlier sources in
  a merge sequence take precedence over later sources.
- `!delete` removes inherited keys, `!replace` replaces a mapping, and `!spread` inserts
  anchored or included sequence items at their declared position.
- Reusable immutable include-limit profiles, configurable library defaults, per-load
  overrides, and `None` for individual unbounded resources. Default budgets allow 32
  include levels, 1,024 reads, 8 MiB per file, and 64 MiB total.
- Typed public APIs, runnable examples, and tests on Python 3.10–3.15 on Linux and Windows.
- MIT licensing, credited to `sheepfling`.
