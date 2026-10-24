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

## Parser integration

The package uses PyYAML's SafeLoader API and adds only two local constructors:
!include for reading a bounded local YAML file and !delete for removing a key
when applying an overlay. The ordinary dispatch path remains safe and rejects
Python-specific object tags. The
[PyYAML project README](https://github.com/yaml/pyyaml) recommends safe_load
for input that is not trusted; [PyPI](https://pypi.org/project/PyYAML/) listed
6.0.3 as the current release when this research was checked.

The package also checks duplicate explicit mapping keys because the YAML
specification requires unique keys. The
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

- A file opts into the adapter with the package header %LOADER LoadInclude, or
  a caller explicitly selects load_yaml_include.
- An include path is resolved from the containing file first, followed by
  configured search paths. Canonical resolved paths must remain within an
  allowed root. Symlinks that resolve outside those roots are rejected.
- The base mapping comes from one <<: !include value per mapping. The current
  mapping overlays it recursively; the current file wins.
- If both values are mappings, they merge recursively. Other values replace
  the previous value, including sequences, null, and type changes.
- !delete removes a key during an overlay. Without a mapping overlay, the tag
  is rejected rather than leaking an internal sentinel into application data.
- Duplicate explicit keys, include cycles, non-mapping overlay targets, and
  filesystem-boundary violations are errors.
- Maximum include depth, file count, per-file bytes, and total included bytes
  keep the file-loading surface bounded. URLs are not fetched, and no
  arbitrary object constructors are registered.
- Errors retain YAML source line and column marks. The returned Python values
  do not keep key-by-key provenance; callers needing that should use a
  source-aware representation or a round-trip YAML library.

The package-specific <<: !include operation deliberately does not accept
<<: *anchor. Ordinary YAML passed through the safe dispatch path continues to
use PyYAML's legacy anchor-merge compatibility behavior. This keeps same-file
aliasing distinct from layered-file configuration.
