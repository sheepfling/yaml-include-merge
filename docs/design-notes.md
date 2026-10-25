# YAML include and overlay design notes

Research checked 2026-10-24. The loader uses the YAML implementation already
used by the application; the design does not introduce a parser or a second
configuration language.

## YAML concepts

The [YAML 1.2.2 specification](https://yaml.org/spec/1.2.2/) defines mappings,
sequences, and scalars. Mapping keys must be unique. Anchors and aliases let a
node in one YAML representation be referenced elsewhere in that document;
they are not file imports. Mapping key order is not application data, and
comments are presentation details that are not part of the representation.

The [YAML 1.2.2 change notes](https://yaml.org/spec/1.2.2/ext/changes/) state
that the special << and = mapping keys were removed from YAML 1.2. Some
implementations retain << as a compatibility extension. Configuration file
includes, overlays across files, YAML anchor merges, and Git's text conflict
merging therefore need separate names and documented semantics.

## Why these semantics

The priority rule for `<<` comes from the [YAML 1.1 merge-key working draft](https://yaml.org/type/merge.html):
earlier maps in a merge sequence win, and keys already in the current mapping
take precedence. YAML 1.2 removed the special `<<` key, but PyYAML retains it
in [`SafeConstructor.flatten_mapping`](https://github.com/yaml/pyyaml/blob/main/lib/yaml/constructor.py).
This package keeps that shallow behavior and gives `<<<` the same precedence
while recursively combining mapping values. Recursion is the extension; the
priority rule stays familiar.

The other choices follow YAML's data model: [sequences are ordered and mappings
are unordered collections of unique keys](https://yaml.org/spec/1.2.2/). Source
priority therefore belongs in a sequence of mappings, not in mapping key
position, and `!spread` inserts sequence items at an explicit position.
Anchors and aliases refer to nodes in a document; they do not import files, so
`!include` makes cross-file loading explicit. `!delete` and `!replace` are
package-defined tags confined to overlays. `null` remains an ordinary value,
and deep recursion applies only to mappings; sequences and scalars replace as
whole values.

## Parser integration

The package uses PyYAML's SafeLoader by default and adds local constructors for
`!include` (read a bounded local YAML file), `!delete` (remove an inherited key
during an overlay), `!replace` (replace one mapping value in a deep overlay),
and `!spread` (insert one sequence source at a list position).
The ordinary dispatch path remains safe and rejects Python-specific object tags.
PyYAML's implicit scalar resolution still treats values such as `on` and `yes` as
booleans and `010` as octal 8; this adapter does not implement the YAML 1.2
core schema. The
[PyYAML project README](https://github.com/yaml/pyyaml) recommends safe_load
for input that is not trusted; [PyPI](https://pypi.org/project/PyYAML/) listed
6.0.3 as the current release when this research was checked.

The package also checks duplicate explicit mapping keys because the YAML
specification requires unique keys. Callers can explicitly select PyYAML's
FullLoader, Loader, or UnsafeLoader instead; the selected class is wrapped to
keep duplicate-key checks and package tags. As with the loader selected by a
caller, constructors and non-safe loader behavior must be trusted. The
[ruamel.yaml API notes](https://yaml.dev/doc/ruamel.yaml/api/) document that
its loader rejects duplicate keys by default and that PyYAML does not enforce
the uniqueness rule. This implementation adds that check on top of SafeLoader
without changing PyYAML's constructor model.

Include lookup is behind a small `IncludeResolver` protocol. The built-in
filesystem resolver enforces canonical allowed roots; the in-memory resolver
uses an explicit namespace. The loader retains source identities for nested
relative lookup and cycle detection, owns and closes returned streams, enforces
the same depth/count/byte limits, and performs all parsing and merging. An
injected resolver is trusted application code and must honor its requested
roots; the built-in implementation does not perform network access.

The supported Python alternative for round-trip editing is
[ruamel.yaml](https://yaml.dev/doc/ruamel.yaml/). Its round-trip API preserves
comments, ordering, and hand-written anchors. Its safe mode is available
through YAML(typ='safe'). It is a better fit when source formatting is part of
the application contract; this package returns native Python mappings and
sequences, so comments and per-key origin metadata are lost.

For JavaScript applications, [eemeli/yaml](https://eemeli.org/yaml/) offers
parseDocument and lower-level parser APIs, YAML 1.1 and 1.2 schemas, comment
editing, and structured errors. It is a maintained alternative when the host
application is JavaScript rather than Python.

[pyyaml-include](https://pyyaml-include.readthedocs.io/) is useful prior art for
tag-based inclusion. It supports file-system backends through fsspec, including
remote schemes, and its PyPI metadata declares GPLv3+. This repository does
not copy it: remote includes are out of scope here, and the project keeps its
own adapter around PyYAML.

## Semantics implemented here

- A file opts into the adapter with the package-specific `%LOADER YamlCompose`
  header (not a standardized YAML directive), or
  a caller explicitly selects load_yaml_compose. An unknown `%LOADER` value
  is an error; explicit `loader_tag=None` selects ordinary YAML.
- Include names without an explicit relative prefix use `include_search`:
  `local-first` (default), `search-first`, `local-only`, or `search-only`.
  Local lookup means the containing file's directory, recalculated at every
  nested include. Configured search paths retain their order. Explicit `./`
  and `../` references use only the containing file; they require a source
  and never fall back to search paths. Absolute names identify exact files.
  Canonical resolved paths must remain within an allowed root, including
  after symlink resolution. Lookup policy never changes that boundary or
  mapping merge precedence. Custom resolvers receive the policy in each
  `IncludeRequest`; the built-in memory resolver uses its namespace root as
  its search location.
- `!include` used as a value loads that value without merging. `<<` uses a
  shallow mapping underlay; `<<<` uses a recursive mapping underlay. Each key
  accepts an alias, `!include` file, or a list of those sources. Only one merge
  key is permitted in a mapping. Sources form a priority list: earlier sources
  win over later ones, and explicit local keys win over all sources regardless
  of the merge key's position. Both merge keys use this rule, matching ordinary
  YAML's shallow merge precedence.
- Every file resolves to a completed value. Delete and replace instructions
  act on that file's inherited mapping and are not carried into future merges.
  Chaining defaults, regional, and production files supplies successive local
  overrides without an ordered patch language.
- With `<<`, a local mapping replaces the source mapping at the same key. With
  `<<<`, those mappings merge recursively. Sequences, null, and type changes
  replace previous values in either mode. A local sequence may contain spread
  operators that insert source sequence items at their positions.
- `!delete` removes a key during an overlay and is a no-op for a missing key.
  It takes no value. A shallow `<<` overlay can delete a direct key, while a
  nested `!delete` requires the recursive overlay created by `<<<`. Without
  an overlay at that level, the tag is rejected rather than leaking an
  internal marker into application data. It is invalid as a mapping key,
  list item, or standalone value.
- `!replace {key: value}` prevents recursive merging at that mapping value in
  a deep overlay. An anchor or file mapping may supply the value through
  `!replace [*name]` or `!replace [!include path.yaml]`. The tag accepts only
  mappings; `!replace {}` clears inherited mapping keys. It is rejected outside
  a deep overlay and has no effect on other keys in that overlay.
- `!spread [*name]` refers to a sequence anchor defined earlier in the same
  document; `!spread [!include path.yaml]` uses the include resolver and resource
  limits to read a sequence. The bracketed sequence contains exactly one source,
  and that source must be a sequence. Both forms work as list items without a
  mapping overlay. Bare `!spread` has no source and is rejected.
- Duplicate explicit keys within one file, include cycles, non-mapping merge
  sources, and filesystem-boundary violations are errors. A key
  present in both the included and local mappings is an override, not a
  duplicate key in one mapping.
- Maximum include depth, file count, per-file bytes, and total included bytes
  bound included content by default. Each bound can be explicitly disabled
  with `None`; the top-level document is not byte-limited. URLs are not fetched,
  and no arbitrary object constructors are registered by the package.
- Errors retain YAML source line and column marks. The returned Python values
  do not keep key-by-key provenance; callers needing that should use a
  source-aware representation or a round-trip YAML library.

`<<: *anchor` keeps PyYAML's shallow merge behavior in composition documents.
This package also accepts file sources. `<<<` is its package-specific deep
counterpart for either source type. Both use first-source-wins priority lists;
the only difference is whether mapping values merge recursively. Ordinary YAML
passed through the safe dispatch path treats `<<<` as a plain mapping key.
